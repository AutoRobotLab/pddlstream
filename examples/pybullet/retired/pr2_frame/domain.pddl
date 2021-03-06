(define (domain pr2-tamp)
  (:requirements :strips :equality)
  (:constants world)
  (:predicates
    (Arm ?a)
    (Stackable ?o ?r)
    (Sink ?r)
    (Stove ?r)

    (BTraj ?t)
    (Grasp ?o ?g)
    (Kin ?a ?o ?p ?g ?q ?t)
    (BaseMotion ?q1 ?t ?q2)
    (Supported ?o ?p ?r)

    (BTrajCollision ?t ?f1 ?q1 ?o1 ?p1 ?f2 ?q2 ?o2 ?p2)

    (AtBConf ?q)
    (AtAConf ?a ?q)
    (AtPose ?f ?o ?p)
    (HandEmpty ?a)
    (CanMove)
    (Cleaned ?o)
    (Cooked ?o)

    (On ?o ?r)
    (Holding ?a ?o)
    (UnsafeBTraj ?t)
  )

  (:action move_base
    :parameters (?q1 ?q2 ?t)
    :precondition (and (BaseMotion ?q1 ?t ?q2)
                       (AtBConf ?q1) (CanMove) (not (UnsafeBTraj ?t)))
    :effect (and (AtBConf ?q2)
                 (not (AtBConf ?q1)) (not (CanMove)))
  )
  (:action pick
    :parameters (?a ?o ?p ?g ?q ?t)
    :precondition (and (Kin ?a ?o ?p ?g ?q ?t)
                       (AtPose world ?o ?p) (HandEmpty ?a) (AtBConf ?q))
    :effect (and (AtPose ?a ?o ?g) (CanMove)
                 (not (AtPose world ?o ?p)) (not (HandEmpty ?a)))
  )
  (:action place
    :parameters (?a ?o ?p ?g ?q ?t)
    :precondition (and (Kin ?a ?o ?p ?g ?q ?t)
                       (AtPose ?a ?o ?g) (AtBConf ?q))
    :effect (and (AtPose world ?o ?p) (HandEmpty ?a) (CanMove)
                 (not (AtPose ?a ?o ?g)))
  )

  (:action clean
    :parameters (?o ?r)
    :precondition (and (Stackable ?o ?r) (Sink ?r)
                       (On ?o ?r))
    :effect (Cleaned ?o)
  )
  (:action cook
    :parameters (?o ?r)
    :precondition (and (Stackable ?o ?r) (Stove ?r)
                       (On ?o ?r) (Cleaned ?o))
    :effect (and (Cooked ?o)
                 (not (Cleaned ?o)))
  )

  (:derived (On ?o ?r)
    (exists (?p) (and (Supported ?o ?p ?r)
                      (AtPose world ?o ?p)))
  )
  (:derived (Holding ?a ?o)
    (exists (?g) (and (Arm ?a) (Grasp ?o ?g)
                      (AtPose ?a ?o ?g)))
  )

  (:derived (UnsafeBTraj ?t)
    (exists (?f1 ?q1 ?o1 ?p1 ?f2 ?q2 ?o2 ?p2) (and (BTraj ?t) (Arm ?f1) (not (= ?f1 ?f2))
            (BTrajCollision ?t ?f1 ?q1 ?o1 ?p1 ?f2 ?q2 ?o2 ?p2)
            (AtAConf ?f1 ?q1) (AtPose ?f1 ?o1 ?p1) (AtAConf ?f2 ?q2) (AtPose ?f2 ?o2 ?p2)))
  )
)