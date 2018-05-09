import time
from collections import defaultdict
from itertools import product

from pddlstream.algorithm import parse_problem, get_optimistic_constraints
from pddlstream.context import ConstraintSolver
from pddlstream.conversion import revert_solution, evaluation_from_fact, substitute_expression
from pddlstream.function import Function, Predicate, PredicateResult, Result
from pddlstream.incremental import process_stream_queue
from pddlstream.instantiation import Instantiator
from pddlstream.object import Object
from pddlstream.reorder import dynamic_programming
from pddlstream.scheduling.relaxed import relaxed_stream_plan
from pddlstream.scheduling.simultaneous import simultaneous_stream_plan, evaluations_from_stream_plan
from pddlstream.stream import StreamResult
from pddlstream.utils import INF, elapsed_time
from pddlstream.visualization import clear_visualizations, create_visualizations

##################################################

# TODO: namedtuple
class ActionInfo(object):
    def __init__(self, terminal=False, p_success=1):
        """
        :param terminal: Indicates the action may require replanning after use
        :param p_success:
        """
        self.terminal = terminal # TODO: infer from p_success
        self.p_success = p_success
        # TODO: should overhead just be cost here then?

def get_action_info(action_info):
    action_execution = defaultdict(ActionInfo)
    for name, info in action_info.items():
        action_execution[name] = info
    return action_execution

def update_stream_info(externals, stream_info):
    for external in externals:
        if external.name in stream_info:
            external.info = stream_info[external.name]

##################################################

def populate_results(evaluations, streams, max_time):
    #start_time = time.time()
    instantiator = Instantiator(evaluations, streams)
    stream_results = []
    while instantiator.stream_queue: # and (elapsed_time(start_time) < max_time):
        stream_results += optimistic_process_stream_queue(instantiator)
    return stream_results

def eagerly_evaluate(evaluations, externals, num_iterations, max_time, verbose):
    start_time = time.time()
    instantiator = Instantiator(evaluations, externals)
    for _ in range(num_iterations):
        for _ in range(len(instantiator.stream_queue)):
            if max_time <= elapsed_time(start_time):
                break
            process_stream_queue(instantiator, evaluations, verbose=verbose)

def optimistic_process_stream_queue(instantiator):
    stream_instance = instantiator.stream_queue.popleft()
    stream_results = stream_instance.next_optimistic()
    for stream_result in stream_results:
        for fact in stream_result.get_certified():
            instantiator.add_atom(evaluation_from_fact(fact))
    return stream_results

##################################################

def ground_stream_instances(stream_instance, bindings, evaluations, opt_evaluations):
    # TODO: combination for domain predicates
    combined_evaluations = evaluations | opt_evaluations
    real_instances = []
    opt_instances = []
    input_objects = [[i] if isinstance(i, Object) else bindings[i]
                    for i in stream_instance.input_objects]
    for combo in product(*input_objects):
        mapping = dict(zip(stream_instance.input_objects, combo))
        domain = set(map(evaluation_from_fact, substitute_expression(
            stream_instance.get_domain(), mapping)))
        if domain <= combined_evaluations:
            instance = stream_instance.external.get_instance(combo)
            if domain <= evaluations:
                real_instances.append(instance)
            else:
                opt_instances.append(instance)
    return real_instances, opt_instances

##################################################

def disable_stream_instance(stream_instance, disabled):
    disabled.append(stream_instance)
    stream_instance.disabled = True

def reset_disabled(disabled):
    for stream_instance in disabled:
        stream_instance.disabled = False
    disabled[:] = []

##################################################

def process_stream_plan(evaluations, stream_plan, disabled, verbose,
                        quick_fail=True, layers=False, max_values=INF):
    # TODO: can also use the instantiator and operate directly on the outputs
    # TODO: could bind by just using new_evaluations
    streams_from_output = defaultdict(list)
    for result in stream_plan:
        if isinstance(result, StreamResult):
            for obj in result.output_objects:
                streams_from_output[obj].append(result)
    shared_output_streams = {s for streams in streams_from_output.values() if 1 < len(streams) for s in streams}

    opt_bindings = defaultdict(list)
    opt_evaluations = set()
    opt_results = []
    failed = False
    for step, opt_result in enumerate(stream_plan):
        if failed and quick_fail:  # TODO: check if satisfies target certified
            break
        # Could check opt_bindings to see if new bindings
        real_instances, opt_instances = ground_stream_instances(opt_result.instance, opt_bindings, evaluations, opt_evaluations)
        #num_instances = min(len(real_instances), max_values) if (layers or all(isinstance(o, Object)
        #                                             for o in opt_result.instance.input_objects)) else 0
        num_instances = min(len(real_instances), max_values) \
            if (layers or (step == 0) or (opt_result not in shared_output_streams)) else 0
        opt_instances += real_instances[num_instances:]
        real_instances = real_instances[:num_instances]
        new_results = []
        for instance in real_instances:
            results = instance.next_results(verbose=verbose, stream_plan=stream_plan[step:])
            evaluations.update(evaluation_from_fact(f) for r in results for f in r.get_certified())
            disable_stream_instance(instance, disabled)
            failed |= not results
            if isinstance(opt_result, PredicateResult) and not any(opt_result.value == r.value for r in results):
                failed = True # TODO: check for instance?
            new_results += results
        for instance in opt_instances:
            results = instance.next_optimistic()
            opt_evaluations.update(evaluation_from_fact(f) for r in results for f in r.get_certified())
            opt_results += results
            failed |= not results
            new_results += results
        for result in new_results:
            if isinstance(result, StreamResult): # Could not add if same value
                for opt, obj in zip(opt_result.output_objects, result.output_objects):
                    opt_bindings[opt].append(obj)
    if verbose:
        print('Success: {}'.format(not failed))
    if failed:
        return None
    return opt_results

##################################################

from collections import defaultdict
from pddlstream.conversion import pddl_from_object, get_prefix, EQ
from pddlstream.fast_downward import get_problem, task_from_domain_problem, apply_action, is_applicable, get_init, \
    fd_from_fact
from pddlstream.scheduling.simultaneous import evaluations_from_stream_plan
from pddlstream.utils import Verbose, INF, MockSet, find
from pddlstream.function import PredicateResult
from pddlstream.scheduling.relaxed import instantiate_axioms, get_achieving_axioms, extract_axioms

def instantiate_plan(evaluations, stream_plan, action_plan, goal_expression, domain):
    if action_plan is None:
        return None
    # TODO: could just do this within relaxed
    # TODO: propositional stream instances
    # TODO: do I want to strip the fluents and just do the total ordering?
    negative_results = filter(lambda r: isinstance(r, PredicateResult) and (r.value == False), stream_plan)
    negative_init = set(get_init((evaluation_from_fact(f) for r in negative_results
                                  for f in r.get_certified()), negated=True))
    #negated_from_name = {r.instance.external.name for r in negative_results}
    opt_evaluations = evaluations_from_stream_plan(evaluations, stream_plan)

    import pddl_to_prolog
    import build_model
    import axiom_rules
    import pddl
    import instantiate

    task = task_from_domain_problem(domain, get_problem(opt_evaluations, goal_expression, domain, unit_costs=False))
    actions = task.actions
    task.actions = []
    type_to_objects = instantiate.get_objects_by_type(task.objects, task.types)
    function_assignments = {f.fluent: f.expression for f in task.init
                            if isinstance(f, pddl.f_expression.FunctionAssignment)}
    task.init = (set(task.init) | {a.negate() for a in negative_init}) - set(function_assignments)

    # TODO: something that inverts the negative items
    stream_instances = [] # TODO: could even apply these to the state directly
    for result in stream_plan:
        name = result.instance.external.name
        precondition = list(map(fd_from_fact, result.instance.get_domain()))
        effects = [([], fd_from_fact(fact)) for fact in result.get_certified() if not get_prefix(fact) == EQ]
        cost = None # TODO: effort?
        instance = pddl.PropositionalAction(name, precondition, effects, cost)
        stream_instances.append(instance)

    action_instances = []
    for name, objects in action_plan:
        # TODO: just instantiate task?
        with Verbose(False):
            model = build_model.compute_model(pddl_to_prolog.translate(task)) # Changes based on init
        #fluent_facts = instantiate.get_fluent_facts(task, model)
        fluent_facts = MockSet()
        init_facts = task.init
        instantiated_axioms = instantiate_axioms(task, model, init_facts, fluent_facts)

        # TODO: what if more than one action of the same name due to normalization?
        action = find(lambda a: a.name == name, actions)
        args = map(pddl_from_object, objects)
        assert(len(action.parameters) == len(args))
        variable_mapping = {p.name: a for p, a in zip(action.parameters, args)}
        instance = action.instantiate(variable_mapping, init_facts,
                           fluent_facts, type_to_objects,
                           task.use_min_cost_metric, function_assignments)
        assert(instance is not None)

        goal_list = [] # TODO: include the goal?
        with Verbose(False): # TODO: helpful_axioms prunes axioms that are already true (e.g. not Unsafe)
            helpful_axioms, axiom_init, _ = axiom_rules.handle_axioms([instance], instantiated_axioms, goal_list)
        axiom_from_atom = get_achieving_axioms(task.init | negative_init,
                                               helpful_axioms, axiom_init)
                                               #negated_from_name=negated_from_name)

        axiom_plan = []
        extract_axioms(axiom_from_atom, instance.precondition, axiom_plan)
        # TODO: what the propositional axiom has conditional derived
        axiom_pre = {p for ax in axiom_plan for p in ax.condition}
        axiom_eff = {ax.effect for ax in axiom_plan}
        instance.precondition = list((set(instance.precondition) | axiom_pre) - axiom_eff)

        assert(is_applicable(task.init, instance))
        apply_action(task.init, instance)
        action_instances.append(instance)

    #combined_instances = stream_instances + action_instances
    orders = set()
    for i, a1 in enumerate(action_plan):
        for a2 in action_plan[i+1:]:
            orders.add((a1, a2))
    # TODO: just store first achiever here
    for i, instance1 in enumerate(stream_instances):
        for j in range(i+1, len(stream_instances)):
            effects = {e for _, e in  instance1.add_effects}
            if effects & set(stream_instances[j].precondition):
                orders.add((stream_plan[i], stream_plan[j]))
    for i, instance1 in enumerate(stream_instances):
        for j, instance2 in enumerate(action_instances):
            effects = {e for _, e in  instance1.add_effects}
            if effects & set(instance2.precondition):
                orders.add((stream_plan[i], action_plan[j]))
    return stream_instances, action_instances

def separate_plan(combined_plan, action_info):
    if combined_plan is None:
        return None, None
    stream_plan = []
    action_plan = []
    terminated = False
    for operator in combined_plan:
        if not terminated and isinstance(operator, Result):
            stream_plan.append(operator)
        else:
            action_plan.append(operator)
            name, _ = operator
            terminated |= action_info[name].terminal
    return stream_plan, action_plan

##################################################

def solve_focused(problem, stream_info={}, action_info={}, max_time=INF, max_cost=INF,
                  commit=True, effort_weight=None, eager_layers=1,
                  visualize=False, verbose=True, **search_kwargs):
    """
    Solves a PDDLStream problem by first hypothesizing stream outputs and then determining whether they exist
    :param problem: a PDDLStream problem
    :param action_info: a dictionary from stream name to ActionInfo for planning and execution
    :param stream_info: a dictionary from stream name to StreamInfo altering how individual streams are handled
    :param max_time: the maximum amount of time to apply streams
    :param max_cost: a strict upper bound on plan cost
    :param commit: if True, it commits to instantiating a particular partial plan-skeleton.
    :param effort_weight: a multiplier for stream effort compared to action costs
    :param eager_layers: the number of eager stream application layers per iteration
    :param visualize: if True, it draws the constraint network and stream plan as a graphviz file
    :param verbose: if True, this prints the result of each stream application
    :param search_kwargs: keyword args for the search subroutine
    :return: a tuple (plan, cost, evaluations) where plan is a sequence of actions
        (or None), cost is the cost of the plan, and evaluations is init but expanded
        using stream applications
    """
    # TODO: return to just using the highest level samplers at the start
    start_time = time.time()
    num_iterations = 0
    best_plan = None; best_cost = INF
    evaluations, goal_expression, domain, externals = parse_problem(problem)
    action_info = get_action_info(action_info)
    update_stream_info(externals, stream_info)
    eager_externals = filter(lambda e: e.info.eager, externals)
    constraint_solver = ConstraintSolver(problem[3])
    disabled = []
    if visualize:
        clear_visualizations()
    #functions = filter(lambda s: isinstance(s, Function), externals)
    functions = filter(lambda s: type(s) is Function, externals)
    negative = filter(lambda s: type(s) is Predicate and s.is_negative(), externals)
    streams = filter(lambda s: s not in (functions + negative), externals)
    stream_results = populate_results(evaluations, streams, max_time-elapsed_time(start_time))
    depth = 0
    while elapsed_time(start_time) < max_time:
        if stream_results is None:
            stream_plan, action_plan, cost = None, None, INF
        else:
            num_iterations += 1
            print('\nIteration: {} | Depth: {} | Evaluations: {} | Cost: {} | Time: {:.3f}'.format(
                num_iterations, depth, len(evaluations), best_cost, elapsed_time(start_time)))
            # TODO: constrain to use previous plan to some degree
            eagerly_evaluate(evaluations, eager_externals, eager_layers, max_time - elapsed_time(start_time), verbose)
            stream_results += populate_results(evaluations_from_stream_plan(evaluations, stream_results),
                                               functions, max_time-elapsed_time(start_time))
            # TODO: warning check if using simultaneous_stream_plan or relaxed_stream_plan with non-eager functions
            solve_stream_plan = relaxed_stream_plan if effort_weight is None else simultaneous_stream_plan
            #solve_stream_plan = sequential_stream_plan if effort_weight is None else simultaneous_stream_plan
            combined_plan, cost = solve_stream_plan(evaluations, goal_expression, domain, stream_results,
                                                               negative, max_cost=best_cost, **search_kwargs)
            stream_plan, action_plan = separate_plan(combined_plan, action_info)
            reorder_streams = dynamic_programming
            stream_plan = reorder_streams(stream_plan)
            # TODO: no point not deferring streams for as long as possible unless really thinking about failure prob
            print('Stream plan: {}\n'
                  'Action plan: {}'.format(stream_plan, action_plan))
            instantiate_plan(evaluations, stream_plan, action_plan, goal_expression, domain)
            raw_input('Continue?')

        if stream_plan is None:
            if disabled or (depth != 0):
                if depth == 0:
                    reset_disabled(disabled)
                stream_results = populate_results(evaluations, streams, max_time - elapsed_time(start_time))
                depth = 0 # Recurse on problems
            else:
                break
        elif len(stream_plan) == 0:
            if cost < best_cost:
                best_plan = action_plan; best_cost = cost
                if best_cost < max_cost:
                    break
            stream_results = None
        else:
            if visualize:
                create_visualizations(evaluations, stream_plan, num_iterations)
            constraint_facts = constraint_solver.solve(get_optimistic_constraints(evaluations, stream_plan), verbose=verbose)
            evaluations.update(map(evaluation_from_fact, constraint_facts))
            if constraint_facts:
                stream_results = []
            else:
                stream_results = process_stream_plan(evaluations, stream_plan, disabled, verbose)
            if not commit:
                stream_results = None
            depth += 1
    # TODO: modify streams here
    return revert_solution(best_plan, best_cost, evaluations)