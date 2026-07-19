# ABMA Outer5 Experiment Report

All 36 runs are non-formal sequential cold processes using seeds 78–80; collision audit and parallel execution are disabled. ABMA order alternates by seed. No process-level cache is shared.

Every ABMA1 run realized 72 Primary evaluations and 5 generations; every ABMA20 run realized 252 and 20; every HGA run exactly exhausted its 72/252 budget with `objective_budget`.

The protocol-declared normalization hashes are recorded. The repository's pre-existing strict validator still reports frozen-float drift for all three instances; protected normalization/protocol files were not modified.

| instance | seed | algorithm | objective_evaluation_count | fitness | makespan | load_imbalance | total_idle_distance | solver_wall_clock_time_s | generation_of_final_best |
|---|---|---|---|---|---|---|---|---|---|
| w30 | 78 | ABMA1 | 72 | 1.010605934542888 | 1269.308863646738 | 144.15812496997773 | 71.15430242870491 | 24.075762500055134 | 1 |
| w30 | 78 | ABMA20 | 252 | 0.8699137546281621 | 1247.981379390075 | 100.73300233801228 | 76.89076798539294 | 47.39698620000854 | 19 |
| w30 | 78 | HGA-budget72 | 72 | 3.7167385932436883 | 1636.3616602950501 | 1008.1767668988668 | 59.197398685276966 | 1.728661600034684 | 0 |
| w30 | 78 | HGA-budget252 | 252 | 3.7167385932436883 | 1636.3616602950501 | 1008.1767668988668 | 59.197398685276966 | 1.7960645998828113 | 0 |
| w30 | 79 | ABMA20 | 252 | 0.7886881476711166 | 1247.9271397889602 | 7.347022067290482 | 103.25323498972944 | 49.352163000032306 | 18 |
| w30 | 79 | ABMA1 | 72 | 1.0928454210031089 | 1269.308863646738 | 215.2955015693176 | 64.46737412925695 | 25.949654400115833 | 0 |
| w30 | 79 | HGA-budget252 | 252 | 3.7420447010908915 | 1640.7809185867861 | 1012.5960251906029 | 59.38240501939936 | 3.004116600146517 | 0 |
| w30 | 79 | HGA-budget72 | 72 | 3.7420447010908915 | 1640.7809185867861 | 1012.5960251906029 | 59.38240501939936 | 2.870126400142908 | 0 |
| w30 | 80 | ABMA1 | 72 | 1.0616110106663088 | 1269.308863646738 | 184.09800959077234 | 70.55016860527454 | 25.166757100028917 | 2 |
| w30 | 80 | ABMA20 | 252 | 0.8411868231500879 | 1249.191948324941 | 77.33145640625048 | 74.5570994502873 | 58.52287370013073 | 20 |
| w30 | 80 | HGA-budget72 | 72 | 3.7177935815907244 | 1636.3616602950501 | 1008.1767668988668 | 59.88520681620034 | 3.521775000030175 | 0 |
| w30 | 80 | HGA-budget252 | 252 | 3.7177935815907244 | 1636.3616602950501 | 1008.1767668988668 | 59.88520681620034 | 4.131124499952421 | 0 |
| w45 | 78 | ABMA1 | 72 | 1.0667882872524257 | 1991.868223994422 | 593.6340660758933 | 86.37728411102862 | 80.5414343001321 | 0 |
| w45 | 78 | ABMA20 | 252 | 0.9900513933141646 | 1984.286893130935 | 438.7211873684314 | 89.83413225688663 | 206.4037520000711 | 16 |
| w45 | 78 | HGA-budget72 | 72 | 1.3131507773205529 | 2052.035224479451 | 865.0299821723004 | 96.07872131775146 | 3.3001291998662055 | 0 |
| w45 | 78 | HGA-budget252 | 252 | 1.3131507773205529 | 2052.035224479451 | 865.0299821723004 | 96.07872131775146 | 3.5989941000007093 | 0 |
| w45 | 79 | ABMA20 | 252 | 0.9712202483204979 | 1976.262255903156 | 414.67289830070376 | 96.19584089418744 | 161.8196537999902 | 20 |
| w45 | 79 | ABMA1 | 72 | 1.0338457677475463 | 1991.868223994422 | 498.99753113122915 | 92.52789397215649 | 80.93082619993947 | 1 |
| w45 | 79 | HGA-budget252 | 252 | 1.295380712764493 | 2045.9095517463795 | 863.6972631948972 | 91.79419083381089 | 4.04461759980768 | 0 |
| w45 | 79 | HGA-budget72 | 72 | 1.295380712764493 | 2045.9095517463795 | 863.6972631948972 | 91.79419083381089 | 3.3562588999047875 | 0 |
| w45 | 80 | ABMA1 | 72 | 1.0219521035917327 | 1988.5670931036875 | 473.65831927465297 | 97.20881315331114 | 90.18093369994313 | 2 |
| w45 | 80 | ABMA20 | 252 | 0.9672441656402135 | 1974.759046957762 | 416.2194169944005 | 94.79204182583378 | 173.4072764001321 | 19 |
| w45 | 80 | HGA-budget72 | 72 | 1.3141029254432202 | 2054.291681539805 | 855.5347265786365 | 96.48086666050142 | 3.131993900053203 | 0 |
| w45 | 80 | HGA-budget252 | 252 | 1.3141029254432202 | 2054.291681539805 | 855.5347265786365 | 96.48086666050142 | 2.2390359002165496 | 0 |
| w60 | 78 | ABMA1 | 72 | 1.196765716606657 | 3419.8016706575004 | 2058.581999093539 | 95.95503129777346 | 218.9572711000219 | 5 |
| w60 | 78 | ABMA20 | 252 | 0.9913705187461362 | 3240.1277334350407 | 1494.0027821956824 | 103.90452279473989 | 521.4299118001945 | 20 |
| w60 | 78 | HGA-budget72 | 72 | 1.289172623661267 | 3523.4533229869257 | 2071.667766011622 | 106.6477029012463 | 3.5969127998687327 | 0 |
| w60 | 78 | HGA-budget252 | 252 | 1.289172623661267 | 3523.4533229869257 | 2071.667766011622 | 106.6477029012463 | 4.287843800149858 | 0 |
| w60 | 79 | ABMA20 | 252 | 1.0463537168050627 | 3288.3849725479845 | 1642.325291847206 | 102.02811734239805 | 597.3731845000293 | 16 |
| w60 | 79 | ABMA1 | 72 | 1.186127105897115 | 3349.4643761104144 | 2400.467237173473 | 94.98719490700725 | 246.2739268001169 | 0 |
| w60 | 79 | HGA-budget252 | 252 | 1.289647146362869 | 3522.4407926196427 | 2068.8011616409303 | 108.32716192150659 | 3.6645772999618202 | 0 |
| w60 | 79 | HGA-budget72 | 72 | 1.289647146362869 | 3522.4407926196427 | 2068.8011616409303 | 108.32716192150659 | 3.367187700001523 | 0 |
| w60 | 80 | ABMA1 | 72 | 1.1736792273466667 | 3401.113668264362 | 1957.6135376220564 | 100.69798257535996 | 230.75609809998423 | 1 |
| w60 | 80 | ABMA20 | 252 | 0.991099007497268 | 3240.8863342215213 | 1491.2139398308427 | 103.39710135188453 | 525.3271097999532 | 16 |
| w60 | 80 | HGA-budget72 | 72 | 1.28871204729149 | 3520.518813801758 | 2064.397995976846 | 109.48881749487849 | 3.791008200030774 | 0 |
| w60 | 80 | HGA-budget252 | 252 | 1.28871204729149 | 3520.518813801758 | 2064.397995976846 | 109.48881749487849 | 4.325177100021392 | 0 |

## Exploratory pairwise summaries

| comparison | instance | metric | mean | median | standard_deviation | exploratory_wilcoxon_p | statistical_label |
|---|---|---|---|---|---|---|---|
| ABMA1_vs_ABMA20 | w30 | fitness_relative_degradation | 0.26980676782929636 | 0.26203951542033604 | 0.11216110190483788 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_ABMA20 | w30 | makespan_relative_degradation | 0.01677577322626289 | 0.017089585316638553 | 0.0005822421629083042 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_ABMA20 | w30 | load_imbalance_relative_degradation | 10.038501509111692 | 1.3806354896982416 | 15.82531657975854 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_ABMA20 | w30 | total_idle_distance_relative_degradation | -0.16799557282774766 | -0.07460538770763477 | 0.1801260909995714 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_ABMA20 | w30 | speedup_right_over_left | 2.065302148760538 | 1.9686598171044427 | 0.22771868130875728 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_ABMA20 | w30 | wall_time_ratio_left_over_right | 0.4879328083904175 | 0.5079597761439691 | 0.05093059754458696 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_ABMA20 | w45 | fitness_relative_degradation | 0.06618329782540573 | 0.0644812744949921 | 0.010576892521314591 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_ABMA20 | w45 | makespan_relative_degradation | 0.006236553581155549 | 0.006992268837657799 | 0.0021405201817638183 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_ABMA20 | w45 | load_imbalance_relative_degradation | 0.2314848794512394 | 0.20335216787998675 | 0.11027480885770785 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_ABMA20 | w45 | total_idle_distance_relative_degradation | -0.017038276112394966 | -0.038129994893080485 | 0.036835754694728516 | 0.5 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_ABMA20 | w45 | speedup_right_over_left | 2.1616885231182352 | 1.99948105558956 | 0.3493939686330166 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_ABMA20 | w45 | wall_time_ratio_left_over_right | 0.4701318565264802 | 0.5001297697742595 | 0.06992492464216772 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_ABMA20 | w60 | fitness_relative_degradation | 0.1749948118814762 | 0.18421996033519594 | 0.03765806085634429 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_ABMA20 | w60 | makespan_relative_degradation | 0.04115545838881303 | 0.049439356249847626 | 0.019785655859682925 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_ABMA20 | w60 | load_imbalance_relative_degradation | 0.38409640985931004 | 0.37789703180345796 | 0.07462443111958005 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_ABMA20 | w60 | total_idle_distance_relative_degradation | -0.0572072264084031 | -0.06900962811811998 | 0.027195490373733033 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_ABMA20 | w60 | speedup_right_over_left | 2.361205144701317 | 2.3814231387730445 | 0.07657771475790762 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_ABMA20 | w60 | wall_time_ratio_left_over_right | 0.4238133821311659 | 0.4199169747360478 | 0.013915478485935261 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w30 | fitness_relative_degradation | -0.716833229422592 | -0.7144513305087585 | 0.010278293508368959 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w30 | makespan_relative_degradation | -0.2250067241587876 | -0.22431031327275738 | 0.001206219037548401 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w30 | load_imbalance_relative_degradation | -0.8205962674801883 | -0.8173951080453338 | 0.034924420332112426 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w30 | total_idle_distance_relative_degradation | 0.15523486495316358 | 0.17809008862250567 | 0.06145126820734877 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w30 | speedup_right_over_left | 0.10744737690179311 | 0.1106036464258305 | 0.034177813599201586 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w30 | wall_time_ratio_left_over_right | 10.038245733989385 | 9.04129323322616 | 3.4988765021560457 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w45 | fitness_relative_degradation | -0.203943150184419 | -0.20189813113613578 | 0.017444065681425182 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w45 | makespan_relative_degradation | -0.02924292387600437 | -0.029320647017787783 | 0.0027905453349263186 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w45 | load_imbalance_relative_degradation | -0.39411855623585296 | -0.4222541249171145 | 0.07064424991257379 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w45 | total_idle_distance_relative_degradation | -0.028478646662503874 | 0.007544982938132513 | 0.06278307651394069 | 1.0 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w45 | speedup_right_over_left | 0.0390583754895099 | 0.04097430382936194 | 0.003756594950206955 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w45 | wall_time_ratio_left_over_right | 25.770800399790033 | 24.40553972959526 | 2.6217700995344946 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w60 | fitness_relative_degradation | -0.08040371105604259 | -0.08027004964707332 | 0.008792069093127039 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w60 | makespan_relative_degradation | -0.03748050845473329 | -0.03391691732175467 | 0.010317071218673875 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w60 | load_imbalance_relative_degradation | 0.034091595339867324 | -0.00631653739695721 | 0.11164833284726262 | 1.0 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w60 | total_idle_distance_relative_degradation | -0.10123219682611415 | -0.1002616213250655 | 0.02144416731320783 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w60 | speedup_right_over_left | 0.015509543942214913 | 0.01642746450847767 | 0.0015909006816848144 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA1_vs_HGA72 | w60 | wall_time_ratio_left_over_right | 64.96078022046906 | 60.87366674777677 | 7.0828513534177056 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w30 | fitness_relative_degradation | -0.7763077900944014 | -0.77374031002814 | 0.011854899849615418 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w30 | makespan_relative_degradation | -0.23779292248693096 | -0.23734379161324687 | 0.0014660289882696407 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w30 | load_imbalance_relative_degradation | -0.9387080313707047 | -0.9232957364767286 | 0.04821452834205344 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w30 | total_idle_distance_relative_degradation | 0.42755763203452196 | 0.2988876148795455 | 0.2708741702272509 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w30 | speedup_right_over_left | 0.05645166718258882 | 0.0608710220085906 | 0.016789954780494706 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w30 | wall_time_ratio_left_over_right | 18.99462112189482 | 16.42817825300965 | 6.503118883966385 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w45 | fitness_relative_degradation | -0.253414445699439 | -0.25024339273370755 | 0.009362814169989467 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w45 | makespan_relative_degradation | -0.03525758738161797 | -0.03404221647226429 | 0.003038226638144304 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w45 | load_imbalance_relative_degradation | -0.5087365574563115 | -0.5134979281800741 | 0.014144792938354064 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w45 | total_idle_distance_relative_degradation | -0.011515820102902495 | -0.017504246107265066 | 0.056710530608234305 | 0.75 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w45 | speedup_right_over_left | 0.018447759022097204 | 0.01743666994967935 | 0.0061044241803898764 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w45 | wall_time_ratio_left_over_right | 58.2687791349775 | 57.35040021322359 | 18.736215948060828 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w60 | fitness_relative_degradation | -0.2168640062534323 | -0.2309383546306724 | 0.02443306302111761 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w60 | makespan_relative_degradation | -0.0754292434060424 | -0.07942933822252904 | 0.0077942831591149234 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w60 | load_imbalance_relative_degradation | -0.2542129488390416 | -0.2776519146322751 | 0.04163111235994076 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w60 | total_idle_distance_relative_degradation | -0.04650267289904199 | -0.055637792811845 | 0.018040413644371638 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w60 | speedup_right_over_left | 0.007530343130871185 | 0.008223240944015706 | 0.0012088584026311775 | 0.25 | exploratory only; n=3; not inferential evidence |
| ABMA20_vs_HGA252 | w60 | wall_time_ratio_left_over_right | 135.3591151403205 | 121.60655474016353 | 23.948950941514568 | 0.25 | exploratory only; n=3; not inferential evidence |

## Convergence after generation 5

| instance | seed | fitness_g5 | fitness_g20 | remaining_improvement_after_g5 | generation_of_final_best | primary_evaluation_of_final_best | time_to_best_s |
|---|---|---|---|---|---|---|---|
| w30 | 78 | 1.010605934542888 | 0.8699137546281621 | 0.16173118216168866 | 19 | 234 | 42.26255420013331 |
| w30 | 79 | 1.0928454210031089 | 0.7886881476711166 | 0.38564960590586433 | 18 | 223 | 42.42668670020066 |
| w30 | 80 | 1.0616110106663088 | 0.8411868231500879 | 0.26203951542033604 | 20 | 242 | 53.945368899963796 |
| w45 | 78 | 1.0667882872524257 | 0.9900513933141646 | 0.07750799045026023 | 16 | 201 | 181.10312450001948 |
| w45 | 79 | 1.0338457677475463 | 0.9712202483204979 | 0.0644812744949921 | 20 | 245 | 155.77694289991632 |
| w45 | 80 | 1.0219521035917327 | 0.9672441656402135 | 0.05656062853096488 | 19 | 230 | 162.69970340002328 |
| w60 | 78 | 1.196765716606657 | 0.9913705187461362 | 0.20718308036868002 | 20 | 249 | 514.1476847000886 |
| w60 | 79 | 1.186127105897115 | 1.0463537168050627 | 0.13358139494055266 | 16 | 194 | 500.60488329990767 |
| w60 | 80 | 1.1736792273466667 | 0.991099007497268 | 0.18421996033519594 | 16 | 194 | 445.90883579989895 |

No n=3 result is inferential evidence and no statement of statistical significance is made.
