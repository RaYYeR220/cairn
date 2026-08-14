# CockroachDB vs Postgres + pgvector — the same trust-tier isolation

_Reproduce: `python lab/compare_pgvector.py`. 800 trusted + 200 quarantined
vectors (128-dim); the quarantine is planted near the query, as an attacker would. Retrieve the
top 10 trusted neighbours. Every number below is measured._

| | CockroachDB (prefix-column vector index) | Postgres + pgvector (HNSW) |
|---|---|---|
| quarantine leaked, **no** tier filter | **0** | **10/10** — the poison wins the top-10 |
| tier-filtered query uses the vector index | **yes** — scoped vector search | **no — falls back to a full Seq Scan** |
| quarantine visited during the filtered search | **none** (partitions pruned) | **all 200** (scanned, then discarded) |
| trusted recall vs exact top-10 | **100%** | **100%** |

**Takeaway.** The tier filter forces a choice on pgvector that CockroachDB never has to make. Without
the filter, the HNSW index is fast but the planted poison takes **all 10** result slots — memory is
not isolated. Add `WHERE trust_tier IN (2,3)` and pgvector **abandons the vector index and
sequentially scans every row** (1000 of them, quarantine included): correct,
but the index bought you nothing, and at scale that is a table scan per query. You get fast search
**or** tier isolation, not both — unless you build a separate partial index per tier.

On CockroachDB the trust tier is a **prefix column of the vector index**, so a tier-filtered query
stays an index-served vector search whose traversal never enters the quarantined partitions. Fast
search **and** isolation, from one index — the mechanism this whole project is built on.

<details><summary>pgvector filtered query plan (note the Seq Scan)</summary>

```
Limit  (cost=103.79..103.81 rows=10 width=12)
  ->  Sort  (cost=103.79..105.79 rows=800 width=12)
        Sort Key: ((embedding <=> '[0.02441527,0.0058789244,-0.010092566,0.08217881,-0.022880893,-0.13841785,-0.024505233,0.09157953,0.033323824,0.06374795,-0.09424691,0.004803971,0.018381516,-0.07454891,0.1244693,-0.11543126,0.04698013,-0.050882675,-0.09335413,0.050726324,0.12421499,-0.1093135,-0.02651524,0.1035656,-0.026808273,-0.13861774,0.019624664,0.090180725,-0.10436439,0.119539246,-0.005920875,-0.014530658,0.060685787,0.099407576,0.082819395,0.036962982,-0.081747256,0.041882113,0.12531845,-0.07514246,-0.121730655,0.13802108,-0.061041288,-0.12028448,0.12962064,-0.04296746,0.09708207,-0.07059887,-0.0733794,-0.10781361,-0.11156722,0.121085465,-0.0706823,-0.01953155,0.012903058,-0.033675846,-0.04997846,-0.09412443,-0.081261136,-0.044955224,0.031753175,-0.008314515,0.0058258506,0.123467065,-0.119278386,-0.12762702,0.083385564,0.004965605,0.12901196,-0.01007338,-0.14281543,-0.0083155865,-0.14368129,-0.06447271,-0.046185784,0.13014731,0.07623377,-0.032119222,0.061474536,-0.061712526,-0.09657377,0.025800085,0.11068243,0.013291798,-0.14662492,-0.10338065,-0.11220054,0.118648835,-0.11910457,-0.09917865,0.038454488,-0.14078324,0.08316079,0.118405156,-0.14624323,-0.10432056,-0.020772608,-0.109548114,0.13579522,0.018268965,0.1388806,0.07093239,0.009360793,0.040253956,-0.0036322996,-0.026723284,0.12927073,0.12770642,0.0713545,0.08914939,-0.036306586,8.77667e-05,0.059594303,-0.1457601,0.11968022,0.14412548,0.12574296,0.05533999,0.06101355,0.02600372,0.12378808,0.04342071,0.108136006,0.0039533414,-0.075356655,-0.059750322,0.14671129,0.14645742]'::vector))
        ->  Seq Scan on m  (cost=0.00..86.50 rows=800 width=12)
              Filter: (trust_tier = ANY ('{2,3}'::integer[]))
```
</details>
