"""Tests for the paper-aligned HGA control."""
import copy
import hashlib
from pathlib import Path
import unittest

from generate_welds import load_frozen_weld_instance
from MRTA_HGA_PAPER_ALIGNED_CONTROL import (
    DEFAULT_CONFIG_PATH, PaperAlignedHGA, PaperAlignedHGAIndividual,
    paper_move_candidates, resolve_args,
)

INSTANCE = r"data\instances\selected\instance_w30.xlsx"
HASH = "2fab0e3e566477294710c7f5296eae3f19fffdaf771928347daa371758bb2a62"


class FixedSampleRandom:
    def __init__(self, pair): self.pair = pair
    def sample(self, population, n): return list(self.pair)


class PaperAlignedHGATest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.welds, cls.meta = load_frozen_weld_instance(INSTANCE)

    def solver(self, **kw):
        defaults=dict(alpha_neighbors=5,max_neighbor_checks=8,vnd_mode="bounded",
                      max_local_search_moves=16,enable_project_fallback=False,
                      enable_partition=False)
        defaults.update(kw); return PaperAlignedHGA(self.welds,42,**defaults)

    def decoded(self,s,xu=10,xl=10):
        return s.decode_and_repair_individual(PaperAlignedHGAIndividual(xu,xl),randomize_missing=True)

    def assert_feasible(self,s,ind):
        robots,_=s._assignment(ind.x_up,ind.x_low);s._assert_feasible(ind,robots)

    def test_01_binary_tournament_objective_and_tie(self):
        s=self.solver();a=PaperAlignedHGAIndividual(5,5,fitness=2);b=PaperAlignedHGAIndividual(6,6,fitness=1)
        s.rng=FixedSampleRandom((a,b));self.assertIs(s.binary_tournament([a,b]),b)
        a.fitness=b.fitness=1;s.rng=FixedSampleRandom((a,b));self.assertIs(s.binary_tournament([a,b]),a)
        self.assertEqual(s._paper_counts["binary_tournament_count"],2)
        self.assertEqual(s._paper_counts["binary_tournament_tie_count"],1)

    def test_02_independent_a_b_route_crossover_and_repair(self):
        s=self.solver();a=self.decoded(s,8,12);b=self.decoded(s,12,8)
        child=s.paper_crossover(a,b,1,forced_indices=(0,3))
        self.assert_feasible(s,child);self.assertEqual(child.diagnostics["paper_route_a"],0)
        self.assertEqual(child.diagnostics["paper_route_b"],3)
        self.assertEqual(s._paper_counts["cross_index_route_replacement_count"],1)
        self.assertGreaterEqual(s._paper_counts["route_mapping_rejected_count"],0)
        self.assertGreaterEqual(s._paper_counts["repair_insert_count"],0)

    def artificial(self):
        routes=[["a","b","c"],["d","e","f"],["g","h"],["i","j"]]
        all_ids=set(sum(routes,[]));legal=[set(all_ids) for _ in routes]
        neighbors={x:[y for y in all_ids if y!=x] for x in all_ids}
        return routes,legal,neighbors

    def test_03_m1_is_critical_interroute_relocate(self):
        r,l,n=self.artificial();out,checks,bad=paper_move_candidates(r,l,n,1,0,20)
        self.assertTrue(out);self.assertGreater(checks,0)
        self.assertTrue(all(len(x[0])==2 for x in out))
        strict=[set(x) for x in r];out2,_,bad2=paper_move_candidates(r,strict,n,1,0,20)
        self.assertFalse(out2);self.assertGreater(bad2,0)

    def test_04_m2_is_interroute_swap(self):
        r,l,n=self.artificial();out,_,_=paper_move_candidates(r,l,n,2,0,20)
        self.assertTrue(out);self.assertTrue(all(len(x[0])==3 and len(x[1])==3 for x in out))
        self.assertTrue(any(x[0]!=r[0] and x[1]!=r[1] for x in out))

    def test_05_m3_is_interroute_pair_swap(self):
        r,l,n=self.artificial();out,_,_=paper_move_candidates(r,l,n,3,0,20)
        self.assertTrue(out);self.assertTrue(any(x[0][:2]==r[1][:2] for x in out))

    def test_06_m4_two_opt_order_only(self):
        r,l,n=self.artificial();out,_,_=paper_move_candidates(r,l,n,4,0,20)
        self.assertTrue(out);self.assertTrue(any(x[0]==["b","a","c"] for x in out))

    def test_07_m5_m6_are_distinct_reconnections(self):
        r,l,n=self.artificial();m5,_,_=paper_move_candidates(r,l,n,5,0,20);m6,_,_=paper_move_candidates(r,l,n,6,0,20)
        self.assertTrue(m5);self.assertTrue(m6)
        self.assertNotEqual({tuple(tuple(q) for q in x) for x in m5},
                            {tuple(tuple(q) for q in x) for x in m6})

    def test_08_all_six_use_alpha_filter(self):
        r,l,_=self.artificial();empty={x:[] for x in sum(r,[])}
        for op in range(1,7):
            out,checks,_=paper_move_candidates(r,l,empty,op,0,20)
            self.assertEqual((out,checks),([],0),f"M{op} bypassed candidates")

    def test_09_vnd_restart_and_bounded_budget(self):
        s=self.solver(max_local_search_moves=20);base=self.decoded(s);calls=[];improved=copy.deepcopy(base);improved.fitness=base.fitness-1
        state={"done":False}
        def fake(ind,op,budget):
            calls.append(op)
            if op==2 and not state["done"]:state["done"]=True;return improved,1
            return None,1
        s._paper_neighborhood=fake;s.paper_vnd(base)
        self.assertEqual(calls[:3],[1,2,1]);self.assertIn(6,calls)
        s2=self.solver(max_local_search_moves=2);s2._paper_neighborhood=lambda ind,op,budget:(None,1)
        s2.paper_vnd(self.decoded(s2));self.assertGreater(s2._paper_counts["vnd_budget_stop_count"],0)

    def test_10_route_hamming_ignores_boundary_only(self):
        s=self.solver();a=self.decoded(s);b=copy.deepcopy(a);b.x_up=3;b.x_low=17
        self.assertEqual(s.route_hamming_distance(a,b),0)
        c=copy.deepcopy(a)
        route=next(r for r in c.robot_order_ids if len(r)>=2);route[0],route[1]=route[1],route[0]
        self.assertGreater(s.route_hamming_distance(a,c),0)
        self.assertAlmostEqual(s.route_hamming_distance(a,c),s.route_hamming_distance(c,a))
        self.assertLessEqual(s.route_hamming_distance(a,c),1)

    def test_11_biased_fitness_uses_both_ranks_and_best_survives(self):
        s=self.solver();pool=[self.decoded(s,x,20-x) for x in (6,8,10,12)]
        best=min(pool,key=lambda x:x.fitness);survivors=s.update_paper_pool(pool,3)
        self.assertEqual(len(survivors),3);self.assertTrue(any(x is best for x in survivors))
        self.assertTrue(all(x.objective_rank>0 and x.diversity_rank>0 for x in pool))
        self.assertTrue(all(x.biased_fitness==x.objective_rank+x.diversity_rank for x in pool))

    def test_12_incremental_pool_can_select_new_child(self):
        s=self.solver(enable_paper_vnd=False);original=s.binary_tournament
        def newest(pool):
            if any(x.generation==1 for x in pool):return next(x for x in pool if x.generation==1)
            return original(pool)
        s.binary_tournament=newest
        _,stats=s.run_paper(mu=2,lamb=2,max_generations=1,max_offspring=2,offspring_pool_mode="incremental")
        self.assertGreater(stats["pool_incremental_child_parent_use_count"],0)
        self.assertEqual(stats["population_update_count"],1)

    def test_13_frozen_project_feasibility_and_hash(self):
        self.assertEqual(self.meta["instance_hash"],HASH);self.assertEqual(len(self.welds),30)
        s=self.solver();a=self.decoded(s);self.assert_feasible(s,a)
        self.assertEqual(a.assignment_stats["unassigned_subweld_count"],0)
        self.assertEqual(a.assignment_stats["sum_length_error"],0)

    def test_14_reproducibility(self):
        def run():
            s=self.solver(enable_paper_vnd=False)
            b,st=s.run_paper(mu=2,lamb=1,max_generations=1,max_offspring=1)
            st.pop("algorithm_time_s",None);return b.fitness,b.robot_order_ids,st
        self.assertEqual(run(),run())

    def test_15_ablation_switches_disable_mechanisms(self):
        s=self.solver(enable_paper_vnd=False,enable_alpha=False,
                      enable_biased_fitness=False,enable_project_fallback=False,
                      enable_partition=False,enable_route_crossover=False)
        _,st=s.run_paper(mu=2,lamb=1,max_generations=1,max_offspring=1,
                         offspring_pool_mode="batch")
        self.assertEqual(st["paper_route_crossover_count"],0)
        self.assertEqual(sum(st[f"paper_m{i}_checks"] for i in range(1,7)),0)
        self.assertEqual(st["partition_neighbor_checks"],0)
        self.assertEqual(st["project_fallback_operator_checks"],0)

    def test_16_mutation_is_separated(self):
        s=PaperAlignedHGA(self.welds,42,enable_partition=False,enable_project_fallback=False)
        self.assertEqual(s.route_mutation_rate,0.0)
        a=self.decoded(s);b=s.paper_mutation(a,boundary_rate=1.0,route_rate=0.0)
        self.assert_feasible(s,b);self.assertEqual(s._paper_counts["route_mutation_effective_count"],0)
        c=s.paper_mutation(a,boundary_rate=0.0,route_rate=1.0)
        self.assert_feasible(s,c);self.assertGreater(s._paper_counts["route_mutation_effective_count"],0)

    def test_17_full_and_bounded_vnd_are_distinct(self):
        full=self.solver(vnd_mode="full",max_local_search_moves=20)
        base=self.decoded(full);calls=[];better=copy.deepcopy(base);better.fitness-=1;state={"x":False}
        def fake(ind,op,budget):
            calls.append(op)
            if op==2 and not state["x"]:state["x"]=True;return better,1
            return None,1
        full._paper_neighborhood=fake;full.paper_vnd(base)
        self.assertEqual(calls[:3],[1,2,1]);self.assertEqual(calls[-1],6)
        self.assertEqual(full._paper_counts["vnd_completed_local_optimum_count"],1)
        bounded=self.solver(vnd_mode="bounded",max_neighbor_checks=2,max_local_search_moves=20)
        self.assertEqual(full._neighborhood_check_limit(20),20)
        self.assertEqual(bounded._neighborhood_check_limit(20),2)
        bounded.max_local_search_moves=2
        bounded._paper_neighborhood=lambda ind,op,budget:(None,1)
        bounded.paper_vnd(self.decoded(bounded))
        self.assertEqual(bounded._paper_counts["vnd_completed_local_optimum_count"],0)
        self.assertEqual(bounded._paper_counts["vnd_budget_stop_count"],1)
        self.assertLess(bounded._paper_counts["vnd_total_candidate_checks"],6)

    def test_18_final_rank_is_real_or_null(self):
        s=self.solver();pool=[self.decoded(s,x,20-x) for x in (8,10,12)]
        best=copy.deepcopy(min(pool,key=lambda x:x.fitness))
        self.assertTrue(s.apply_final_best_ranks(best,pool))
        self.assertGreaterEqual(best.objective_rank,1);self.assertGreaterEqual(best.diversity_rank,1)
        self.assertEqual(best.biased_fitness,best.objective_rank+best.diversity_rank)
        historical=copy.deepcopy(best);historical.x_up+=0.12345
        self.assertFalse(s.apply_final_best_ranks(historical,pool))
        self.assertIsNone(historical.objective_rank);self.assertIsNone(historical.biased_fitness)

    def test_19_full_subweld_hamming_identity(self):
        s=self.solver();a=PaperAlignedHGAIndividual(10,10,robot_order_ids=[["parent_seg0","parent_seg1","q"],[],[],[]])
        b=copy.deepcopy(a);b.robot_order_ids[0]=["parent_seg1","parent_seg0","q"]
        self.assertIn((0,"parent_seg0","parent_seg1"),s._paper_edges(a))
        self.assertGreater(s.route_hamming_distance(a,b),0)
        self.assertEqual(s.route_hamming_distance(a,a),0)

    def test_20_fallback_defaults_and_explicit_switches(self):
        direct=PaperAlignedHGA(self.welds,42)
        self.assertFalse(direct.enable_project_fallback);self.assertTrue(direct.enable_partition)
        base=["--instance-path",INSTANCE]
        args=resolve_args(base);self.assertFalse(args.project_fallback_enabled);self.assertTrue(args.partition_neighborhood_enabled)
        args2=resolve_args(base+["--enable-project-fallback","--disable-partition-neighborhood"])
        self.assertTrue(args2.project_fallback_enabled);self.assertFalse(args2.partition_neighborhood_enabled)

    def test_21_operator_feasible_rates(self):
        s=self.solver();s._paper_counts["paper_m1_checks"]=4;s._paper_counts["paper_m1_feasible"]=1
        s.finalize_operator_metrics();self.assertEqual(s._paper_counts["paper_m1_feasible_rate"],0.25)
        self.assertEqual(s._paper_counts["paper_m2_feasible_rate"],0.0)

    def test_22_config_load_override_hash_and_deprecated_mutation(self):
        base=["--instance-path",INSTANCE]
        a=resolve_args(base);b=resolve_args(base)
        expected=hashlib.sha256(Path(DEFAULT_CONFIG_PATH).read_bytes()).hexdigest()
        self.assertEqual(a.config_hash,b.config_hash);self.assertEqual(a.config_hash,expected)
        self.assertEqual(a.mu,20);self.assertEqual(a.route_mutation_rate,0.0)
        override=resolve_args(base+["--mu","3","--boundary-mutation-rate","0.2"])
        self.assertEqual(override.mu,3);self.assertEqual(override.boundary_mutation_rate,0.2)
        deprecated=resolve_args(base+["--mutation-rate","0.4"])
        self.assertEqual(deprecated.boundary_mutation_rate,0.4)
        self.assertEqual(deprecated.route_mutation_rate,0.0)

    def test_23_decode_repair_after_boundary_change(self):
        s=self.solver();a=self.decoded(s,10,10);old=copy.deepcopy(a.robot_order_ids)
        a.x_up,a.x_low=7.25,13.75
        b=s.decode_and_repair_individual(a,old);self.assert_feasible(s,b)
        expected={w.id for route in s._assignment(7.25,13.75)[0] for w in route}
        self.assertEqual(expected,{x for route in b.robot_order_ids for x in route})

    def test_24_candidate_list_is_deterministic_and_bounded(self):
        s=self.solver();a=self.decoded(s);first=s.build_candidate_lists(a);second=s.build_candidate_lists(a)
        self.assertEqual(first,second)
        for task,neighbors in first.items():
            self.assertNotIn(task,neighbors);self.assertLessEqual(len(neighbors),5)

    def test_25_route_cache_identity_includes_current_geometry(self):
        s=self.solver();a=self.decoded(s,10,10);keys_before=set(s.route_cache)
        b=s.decode_and_repair_individual(PaperAlignedHGAIndividual(7.25,13.75),a.robot_order_ids)
        self.assert_feasible(s,b);keys_after=set(s.route_cache)
        self.assertTrue(keys_after-keys_before)

    def test_26_assignment_uniqueness_and_length_conservation(self):
        s=self.solver();a=self.decoded(s,3.3,16.7);self.assert_feasible(s,a)
        flat=[x for route in a.robot_order_ids for x in route]
        self.assertEqual(len(flat),len(set(flat)))
        self.assertEqual(a.assignment_stats["assigned_subweld_count"],a.assignment_stats["subweld_count"])
        self.assertLessEqual(a.assignment_stats["sum_length_error"],1e-8)

    def test_27_budget_exhaustion_during_initialization_closes_cleanly(self):
        s=self.solver(max_objective_evaluations=10)
        best,stats=s.run_paper(mu=20,lamb=1,max_offspring=20)
        self.assertIsNotNone(best)
        self.assertEqual(stats["objective_evaluation_count"],10)
        self.assertTrue(stats["objective_budget_exhausted"])
        self.assertEqual(stats["stop_reason"],"objective_budget")
        self.assertTrue(s.initialization_budget_exhausted)


if __name__=="__main__":unittest.main(verbosity=2)
