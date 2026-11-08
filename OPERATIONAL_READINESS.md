# Operational readiness and system boundaries

## Purpose

The project is a decision-support prototype for airport gate assignment. It
recommends first-time assignments and ordered reassignments. The evaluator is
the authority in the prototype; an authorized airport operator and the
airport's operational systems would remain authoritative in production.

This document separates implemented behavior from production requirements. It
does not claim regulatory certification or approval for live operations.

## Implemented reliability controls

- Two independent planners are called through separate exception boundaries.
  A planner exception is logged and the other planner can still return a plan.
- Every candidate is replayed against a copy of the observation before use.
  Validation covers output shape, finite increasing intervals, gate and flight
  identity, aircraft/gate compatibility, outages, occupancy, reassignment order,
  and changed-flight postconditions.
- An unsafe candidate is rejected rather than compared only by estimated cost.
- Best-fit evaluates reassignment sequencing for each complete repair candidate.
  Cyclic candidates are skipped and the cheapest sequenceable repair is kept.
- Changed bookings receive their new intervals before waiting flights are
  planned, including changed flights that remain at the same gate.
- Waiting flights with no safe option remain unassigned instead of receiving an
  invalid gate. If changed flights cannot be recovered safely, the coordinator
  raises an explicit error naming the affected flights.
- Gate choices and action ordering are deterministic. Searches are bounded and
  no external optimization service or paid solver is required.

## Remaining reliability limits

- Best-fit exact joint repair is local to components of at most ten flights.
  Larger components use a simpler forced-move path and can still be incomplete.
- Not every cyclic exchange has a sequential representation. A temporary gate
  or operator-created intermediate plan may be necessary.
- The prototype does not provide process supervision, network retries,
  distributed timeouts, persistent checkpoints, or automatic service failover.
- Unknown future disruptions cannot be anticipated perfectly. Outage-history
  cost is a heuristic, not a forecast.

## Practical integration path

1. Build a read-only adapter from the airport operational database or gate
   management system into the documented observation schema.
2. Validate schema, aircraft/gate identifiers, time zones, event ordering, and
   occupancy intervals before invoking the planner.
3. Run the stateless decision function within an operational latency budget.
4. Present recommendations and rejection reasons to authorized gate staff.
5. Require human approval before writing changes to operational displays,
   airline systems, baggage systems, or resource-allocation tools.
6. Preserve the last valid plan and switch to documented manual procedures if
   validation fails, data becomes stale, or the decision service is unavailable.
7. Record recommendations, approvals, overrides, input versions, software
   versions, and failures in a tamper-evident operational audit trail.
8. Monitor data quality, latency, invalid recommendations, recovery failures,
   unassigned flights, overrides, and service availability.

Human approval, persistent audit storage, live adapters, monitoring, and manual
fallback procedures are production requirements; they are not represented as
completed features in this evaluator prototype.

## Safety and regulatory boundaries

Implemented hard checks reduce decision errors involving overlapping aircraft,
gate outages, wingspan, jetbridge requirements, and flight/gate categories.
They are not a complete airport safety case.

A production review would need to map applicable requirements and procedures
to design controls, including:

- Transport Canada oversight and applicable Canadian Aviation Regulations.
- GTAA Safety Management System and local operating/change-management procedures.
- CBSA procedures for international arrivals and customs-controlled flows.
- Canada Labour Code and applicable worker/ground-operation safety requirements.
- Privacy, access-control, retention, and audit requirements if personal data is
  introduced. The current prototype primarily processes flight and gate data.
- Applicable ICAO/IATA operational guidance, distinguished from Canadian law.

Ramp/taxi movement, towing, fueling, maintenance, crew availability, detailed
wingtip clearance, and airline-specific procedures are outside the current
model. These checks must not be inferred from a valid prototype assignment.

## Verification and efficiency baseline

The current development verification includes:

- 131 passing automated tests, including seven adversarial recovery tests.
- All 13 shipped scenarios valid with zero unassigned flights.
- Aggregate evaluator score 148.1 across those scenarios: 23.5% below first-fit
  and 27.65% below the scored reference.
- Combined scenario score 51.3; seeded variant scores 51.3, 51.3, and 51.8.
- Approximately 0.647 seconds for all 13 scenarios on the development machine.

These are evaluator metrics, not monetary savings, production service-level
objectives, or proof of live operational performance.

## Pilot measures

A limited shadow-mode pilot should compare recommendations with operator
choices and measure recovery success, invalid recommendations, gate changes,
walking-distance proxies, missed-connection indicators, staff review time,
decision latency, overrides, and manual interventions. Financial return should
only be estimated after obtaining those operational measurements and actual
integration/operating costs.
