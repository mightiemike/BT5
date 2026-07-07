### Title
Wrong Health Type in `burnNlp` Allows Collateral Extraction Below Initial Margin — (`core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.burnNlp` enforces only a `MAINTENANCE` health check after burning NLP and crediting quote. Every other collateral-reducing operation in the same contract enforces an `INITIAL` health check. An attacker whose subaccount sits in the zone where `MAINTENANCE >= 0` but `INITIAL < 0` can burn NLP, receive quote, and leave the subaccount below initial margin — immediately eligible for liquidation and capable of generating bad debt.

---

### Finding Description

`burnNlp` removes NLP from the caller's balance and credits quote (minus a 0.1% fee). The post-state health guard is:

```solidity
// core/contracts/Clearinghouse.sol lines 526-529
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [1](#0-0) 

Every other collateral-reducing path uses `INITIAL`:

| Function | Health type enforced |
|---|---|
| `mintNlp` | `INITIAL` |
| `withdrawCollateral` | `INITIAL` |
| `nlpProfitShare` | `INITIAL` |
| `forceRebalanceNlpPool` | `INITIAL` |

`mintNlp` (the symmetric counterpart) checks `INITIAL`:

```solidity
// core/contracts/Clearinghouse.sol lines 479-482
require(
    getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [2](#0-1) 

`withdrawCollateral` explicitly selects `INITIAL` for all non-X_ACCOUNT senders:

```solidity
// core/contracts/Clearinghouse.sol lines 415-419
IProductEngine.HealthType healthType = sender == X_ACCOUNT
    ? IProductEngine.HealthType.PNL
    : IProductEngine.HealthType.INITIAL;
require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
``` [3](#0-2) 

The gap between `INITIAL` and `MAINTENANCE` health is structural: `INITIAL` uses tighter (lower) long-weights and tighter (higher) short-weights than `MAINTENANCE` (see `RiskHelper._getWeightX18`). A subaccount with leveraged perp positions can legally sit in the band `MAINTENANCE >= 0, INITIAL < 0` — this is the normal "under initial, above maintenance" zone that triggers liquidation eligibility. [4](#0-3) 

---

### Impact Explanation

1. Attacker opens leveraged perp positions so that `INITIAL health < 0` but `MAINTENANCE health >= 0` (the standard liquidation-eligible zone).
2. Attacker holds NLP tokens in the same subaccount.
3. Attacker signs and submits a `BurnNlp` transaction.
4. `burnNlp` removes NLP, credits quote, then checks only `MAINTENANCE >= 0` — the check passes.
5. Post-burn: the subaccount has received quote (real value extracted) while remaining below initial margin.
6. `ClearinghouseLiq.isUnderInitial` returns `true` immediately after the burn, making the subaccount liquidatable.
7. If the perp positions move adversely before liquidation completes, the protocol absorbs bad debt.

The attacker extracts real quote value from a position that the protocol's own initial-margin rules would have blocked under any other collateral-reduction path.

---

### Likelihood Explanation

The precondition — a subaccount in the `MAINTENANCE >= 0, INITIAL < 0` band — is a normal, reachable protocol state (it is exactly the state that triggers liquidation). Any user with leveraged perp positions and NLP holdings can reach it deliberately or find themselves in it due to price movement. The `BurnNlp` flow is a standard signed-order path through the Endpoint, requiring no special privileges.

---

### Recommendation

Replace `HealthType.MAINTENANCE` with `HealthType.INITIAL` in the `burnNlp` post-state guard, consistent with every other collateral-reducing function:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [5](#0-4) 

---

### Proof of Concept

1. Deploy the protocol locally (Hardhat).
2. Open a leveraged short perp position on a subaccount such that after the position is opened, `getHealth(sub, INITIAL) = -ε` and `getHealth(sub, MAINTENANCE) = +δ` (both small positive/negative values achievable by sizing the position to sit just inside the maintenance band).
3. Deposit NLP tokens into the same subaccount (via `mintNlp` while health is still positive, before opening the perp).
4. Submit a signed `BurnNlp` transaction for the NLP balance.
5. Observe: transaction succeeds, quote is credited to the subaccount.
6. Assert: `getHealth(sub, INITIAL) < 0` — the subaccount is below initial margin post-burn.
7. Assert: `ClearinghouseLiq.isUnderInitial(sub) == true` — the subaccount is immediately liquidatable.
8. The attacker has extracted quote from an undercollateralized position, violating the invariant enforced by `withdrawCollateral`, `mintNlp`, `nlpProfitShare`, and `forceRebalanceNlpPool`.

### Citations

**File:** core/contracts/Clearinghouse.sol (L415-419)
```text
        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
```

**File:** core/contracts/Clearinghouse.sol (L479-482)
```text
        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/Clearinghouse.sol (L523-529)
```text
        // Burning NLP can decrease health if the burn fee exceeds the health improvement
        // from the withdrawal. This check prevents malicious actors from deliberately
        // creating unhealthy subaccounts through NLP burns.
        require(
            getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/libraries/RiskHelper.sol (L34-55)
```text
    function _getWeightX18(
        Risk memory risk,
        int128 amount,
        IProductEngine.HealthType healthType
    ) internal pure returns (int128) {
        if (healthType == IProductEngine.HealthType.PNL) {
            return ONE;
        }

        int128 weight;
        if (amount >= 0) {
            weight = healthType == IProductEngine.HealthType.INITIAL
                ? risk.longWeightInitialX18
                : risk.longWeightMaintenanceX18;
        } else {
            weight = healthType == IProductEngine.HealthType.INITIAL
                ? risk.shortWeightInitialX18
                : risk.shortWeightMaintenanceX18;
        }

        return weight;
    }
```
