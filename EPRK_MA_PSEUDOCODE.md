# EPRK-MA 伪代码

```text
INPUT atomic instance A, event sets Eu/El, normalization S, seed, stop rule
BUILD exactly one global 4N² endpoint-transition table
PRECOMPUTE zero successors and same-parent adjacent atoms
INITIALIZE two populations with mixed random/balanced/continuity/geometric keys
EVALUATE each complete chromosome; count every request as one Primary

WHILE stop rule permits another complete request:
    FOR each population:
        rank by fitness; retain elites
        create biased random-key crossover offspring
        create reset/Gaussian/event-jump/block mutants
        evaluate complete offspring under exact event decoder and direction DP
        FOR selected top elites:
            try event indices ±1, ±2 by first improvement
            try bounded critical-route relocate/swap/2-opt/chain moves
            re-encode only keys belonging to the changed robot route
        update population
    every 40 generations migrate two elites
    after 100 stagnant generations restart non-elites
RETURN best fully evaluated phenotype and audited counters
```

```text
DECODE(u_up,u_low,q):
    e_up  = floor(clamp01(u_up)  * |Eu|)
    e_low = floor(clamp01(u_low) * |El|)
    assignment = cached atomic ID sets for (e_up,e_low)
    route_r = stable_sort(assignment_r, key=(q_i, atomic_weld_id))
    assert union(route_r)=all atoms and routes are disjoint
    directions_r = exact_two_state_DP(route_r)
    return phenotype(e_up,e_low,route_1..route_4,directions,metrics)
```

