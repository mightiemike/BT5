### Title
Undercharged Execution Cost for `point_add` and `pubkey_for_exp` Due to Wrong Raspberry Pi Performance Multiplier — (`File: src/more_ops.rs`)

---

### Summary

The cost constants for `op_point_add` and `op_pubkey_for_exp` were updated with the stated goal of modeling Raspberry Pi 4 performance. The inline comments document the measured slowdown factor as **6.39×** (point_add) and **6.32686×** (pubkey). However, the actual constants were only increased by approximately **3.2×** — roughly half the documented ratio. This means the cost model undercharges these operations by ~2× on Raspberry Pi hardware, allowing attacker-crafted CLVM programs to consume significantly more real CPU time than the cost limit implies on constrained validator nodes.

---

### Finding Description

In `src/more_ops.rs`, four constants are defined with explicit comments documenting the Raspberry Pi 4 benchmark ratios used to justify the values:

```
// Raspberry PI 4 is about 7.679960 / 1.201742 = 6.39 times slower
// in the point_add benchmark

// increased from 31592 to better model Raspberry PI
const POINT_ADD_BASE_COST: Cost = 101094;
// increased from 419994 to better model Raspberry PI
const POINT_ADD_COST_PER_ARG: Cost = 1343980;

// Raspberry PI 4 is about 2.833543 / 0.447859 = 6.32686 times slower
// in the pubkey benchmark

// increased from 419535 to better model Raspberry PI
const PUBKEY_BASE_COST: Cost = 1325730;
// increased from 12 to closer model Raspberry PI
const PUBKEY_COST_PER_BYTE: Cost = 38;
```

Applying the documented ratios to the documented old values:

| Constant | Old Value | Documented Ratio | Expected New Value | Actual New Value | Actual Ratio |
|---|---|---|---|---|---|
| `POINT_ADD_BASE_COST` | 31,592 | 6.39× | ~201,873 | 101,094 | **3.20×** |
| `POINT_ADD_COST_PER_ARG` | 419,994 | 6.39× | ~2,683,762 | 1,343,980 | **3.20×** |
| `PUBKEY_BASE_COST` | 419,535 | 6.32686× | ~2,653,000 | 1,325,730 | **3.16×** |
| `PUBKEY_COST_PER_BYTE` | 12 | 6.32686× | ~75.9 | 38 | **3.17×** |

All four constants were increased by approximately **3.2×** — consistently about half the ratio the comments document as the correct Raspberry Pi slowdown factor. The comments state the intent clearly ("to better model Raspberry PI"), but the arithmetic does not match. This is the same class of bug as the Goldigovernor report: a hardcoded constant derived from a wrong assumption about the target system parameter.

---

### Impact Explanation

The CLVM cost model is the primary mechanism by which the Chia network ensures that all full nodes — including Raspberry Pi 4 nodes, which are explicitly supported and benchmarked — can validate a block within the block time. If the cost assigned to `point_add` and `pubkey_for_exp` is ~2× too low relative to Raspberry Pi hardware, then:

1. A CLVM program that fills the block cost limit with `point_add` or `pubkey_for_exp` calls will consume approximately **2× more real CPU time** on a Raspberry Pi than the cost model predicts.
2. Raspberry Pi nodes may be unable to validate blocks within the block time, causing them to fall behind the chain tip.
3. In the worst case, this constitutes a **consensus divergence** risk: Raspberry Pi nodes reject or delay acceptance of blocks that faster nodes accept, fragmenting the network.
4. The corrupted result is the **cost charged** for `op_point_add` / `op_pubkey_for_exp` — it is concretely ~2× lower than the documented target, meaning the cost-to-CPU-time invariant is broken for constrained hardware.

---

### Likelihood Explanation

- `point_add` (opcode 29) and `pubkey_for_exp` (opcode 30) are standard, always-enabled operators in `ChiaDialect` — no flags or softfork guards required.
- Any attacker can submit a transaction whose puzzle maximizes calls to these operators up to the block cost limit. The program is valid and will be accepted by fast nodes.
- Raspberry Pi 4 nodes are explicitly part of the supported validator set (the benchmarks exist precisely because of this).
- The discrepancy is systematic across all four affected constants, making it unlikely to be a deliberate design choice.

---

### Recommendation

Recalculate the four constants using the documented Raspberry Pi slowdown ratios applied to the documented old values:

- `POINT_ADD_BASE_COST`: `31592 × 6.39 ≈ 201,873`
- `POINT_ADD_COST_PER_ARG`: `419994 × 6.39 ≈ 2,683,762`
- `PUBKEY_BASE_COST`: `419535 × 6.32686 ≈ 2,653,000`
- `PUBKEY_COST_PER_BYTE`: `12 × 6.32686 ≈ 76`

Re-run the Raspberry Pi benchmarks to confirm the corrected values keep execution within the block time at the cost limit, and update the comments to reflect the final chosen values and their derivation.

---

### Proof of Concept

The root cause is directly readable from the source. The comments in `src/more_ops.rs` lines 71–85 document the intended multiplier (6.39× and 6.32686×) and the old values (31592, 419994, 419535, 12). Simple arithmetic confirms the actual multiplier applied is ~3.2×:

```
101094  / 31592  = 3.200...   (documented: 6.39×)
1343980 / 419994 = 3.200...   (documented: 6.39×)
1325730 / 419535 = 3.160...   (documented: 6.32686×)
38      / 12     = 3.166...   (documented: 6.32686×)
```

A concrete trigger: craft a CLVM program consisting of repeated `point_add` calls up to the block cost limit (e.g., `(point_add pt pt pt ... pt)` with enough arguments to reach `max_cost`). On a reference machine the program completes well within the block time. On a Raspberry Pi 4, the same program takes ~2× longer than the cost model predicts, because `POINT_ADD_COST_PER_ARG = 1,343,980` should be `~2,683,762` to correctly model that hardware. The attacker-controlled entry is the CLVM bytecode bytes passed to `run_program` via any standard Chia transaction submission path. [1](#0-0)

### Citations

**File:** src/more_ops.rs (L71-85)
```rust
// Raspberry PI 4 is about 7.679960 / 1.201742 = 6.39 times slower
// in the point_add benchmark

// increased from 31592 to better model Raspberry PI
const POINT_ADD_BASE_COST: Cost = 101094;
// increased from 419994 to better model Raspberry PI
const POINT_ADD_COST_PER_ARG: Cost = 1343980;

// Raspberry PI 4 is about 2.833543 / 0.447859 = 6.32686 times slower
// in the pubkey benchmark

// increased from 419535 to better model Raspberry PI
const PUBKEY_BASE_COST: Cost = 1325730;
// increased from 12 to closer model Raspberry PI
const PUBKEY_COST_PER_BYTE: Cost = 38;
```
