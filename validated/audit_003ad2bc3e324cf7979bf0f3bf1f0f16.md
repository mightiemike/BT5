### Title
Flat Minimum Burn Fee in `burnNlp` Allows Fee Reduction via NLP Pooling — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

The `burnNlp` function charges a flat minimum fee of `ONE` per burn transaction regardless of the NLP amount burned. This is structurally identical to the sheepDog bug: multiple users with small NLP positions can pool their NLP into a single shared subaccount and execute one burn, paying one flat fee instead of N flat fees.

---

### Finding Description

In `Clearinghouse.sol`, `burnNlp` computes the fee as:

```solidity
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
```

`ONE` is the protocol's fixed-point unit (`1e18`), representing 1 unit of the quote token (USDC) in 18-decimal internal representation. The fee is therefore:

- **Flat at `ONE` (1 USDC)** for any burn where `quoteAmount < 1000 * ONE` (i.e., NLP worth less than 1000 USDC)
- **Proportional at 0.1%** only for burns worth ≥ 1000 USDC

The fee is charged **per burn transaction** (per subaccount call), not per NLP unit. This is the root cause: the flat minimum is invariant to the amount being burned, so the per-unit cost is inversely proportional to burn size.

**Pooling attack path:**

1. N users each hold NLP worth X USDC (X < 1000 USDC individually).
2. Each user burning individually pays `N × ONE` in total fees.
3. Users route their NLP through a shared subaccount (a contract that accepts quote deposits, mints NLP on their behalf via `mintNlp`, then burns in one call via `burnNlp`).
4. The shared subaccount burns all pooled NLP in a single `burnNlp` call, paying only `max(ONE, N·X/1000)` — which equals `ONE` as long as `N·X < 1000 USDC`.
5. Savings: `(N − 1) × ONE` USDC.

The entry path is fully unprivileged: any user can deposit quote into a shared contract subaccount, and the sequencer will process the resulting `MintNlp`/`BurnNlp` transactions normally.

---

### Impact Explanation

- The protocol collects fewer burn fees than intended when users pool NLP.
- For N users each holding NLP worth < 1000/N USDC, the total fee paid is `ONE` instead of `N × ONE` — a reduction of `(N−1)/N × 100%`.
- The "missing" fee is not redistributed; it remains as unrealized value in the NLP pool, diluting the fee revenue that would otherwise accrue to the protocol/NLP holders.
- The corrupted state delta is `collectedBurnFees` — the protocol receives `ONE` instead of `N × ONE` for N pooled users.

---

### Likelihood Explanation

- **Medium.** Requires coordination between users or deployment of a permissionless pooling contract. The incentive is concrete and quantifiable (save `(N−1)` USDC per pooled burn). A pooling contract is straightforward to deploy on an EVM chain and requires no privileged access.
- The attack is more attractive as N grows or as the NLP price rises (making individual positions worth more but still below the 1000 USDC threshold).

---

### Recommendation

Remove the flat minimum floor from the burn fee, or charge the fee proportionally per NLP unit burned:

```solidity
// Instead of:
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);

// Consider:
int128 burnFee = quoteAmount / 1000; // pure proportional, no flat floor
```

If a minimum fee is required for anti-spam, it should be a separate, explicitly documented mechanism (e.g., a fixed protocol fee charged via `chargeFee`) rather than embedded in the proportional burn fee formula.

---

### Proof of Concept

**Scenario: 5 users, each holding NLP worth 100 USDC**

| Approach | Burn calls | Fee per call | Total fees paid |
|---|---|---|---|
| Individual burns | 5 | `max(1, 100/1000)` = 1 USDC | **5 USDC** |
| Pooled burn (shared subaccount) | 1 | `max(1, 500/1000)` = 1 USDC | **1 USDC** |

Savings: **4 USDC (80% reduction)**.

Root cause line: [1](#0-0) 

The `MathHelper.max(ONE, quoteAmount / 1000)` expression makes the fee flat at `ONE` for any burn below 1000 USDC in quote value, regardless of how many NLP tokens are being burned or how many users contributed to the position.

### Citations

**File:** core/contracts/Clearinghouse.sol (L503-504)
```text
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
```
