The code confirms this is a real vulnerability. Here is the full analysis:

---

### Title
`burnNlp` Uses `MAINTENANCE` Health Check Instead of `INITIAL`, Allowing Voluntary Entry Into Zombie State — (`core/contracts/Clearinghouse.sol`)

### Summary

`Clearinghouse.burnNlp` only enforces a `MAINTENANCE` health check after the burn, while every other voluntary action that reduces health (`withdrawCollateral`, `mintNlp`, `nlpProfitShare`, `forceRebalanceNlpPool`) enforces an `INITIAL` health check. A user can burn NLP such that the burn fee pushes their `INITIAL` health below zero while `MAINTENANCE` health remains non-negative, landing in a state where they cannot open new positions and cannot be liquidated.

### Finding Description

In `Clearinghouse.burnNlp`, after removing the NLP balance and crediting `quoteAmount - burnFee` to the sender, the only post-state health guard is:

```solidity
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [1](#0-0) 

Every comparable voluntary action uses `INITIAL`:

- `withdrawCollateral` — `getHealth(sender, INITIAL) >= 0` [2](#0-1) 

- `mintNlp` — `getHealth(txn.sender, INITIAL) >= 0` [3](#0-2) 

- `nlpProfitShare` — `getHealth(poolSubaccount, INITIAL) >= 0` [4](#0-3) 

- `forceRebalanceNlpPool` — `getHealth(..., INITIAL) >= 0` [5](#0-4) 

`INITIAL` health uses the more conservative `longWeightInitialX18` / `shortWeightInitialX18`, while `MAINTENANCE` uses the looser `longWeightMaintenanceX18` / `shortWeightMaintenanceX18`. [6](#0-5) 

The burn fee is `max(ONE, quoteAmount / 1000)`, so even a minimum-size burn carries a fee of at least `ONE` (1 × 10¹⁸ in fixed-point). [7](#0-6) 

The code comment at lines 523–525 explicitly acknowledges that the burn fee can decrease health, yet the guard chosen (`MAINTENANCE`) is weaker than the protocol-wide standard for voluntary actions. [8](#0-7) 

### Impact Explanation

After a successful `burnNlp` call that passes the `MAINTENANCE >= 0` check but leaves `INITIAL < 0`:

1. **No new positions can be opened** — the off-chain exchange and order-matching logic gate new orders on `INITIAL` health.
2. **No liquidation is possible** — `ClearinghouseLiq.isUnderMaintenance` returns `false`, so the liquidation path is blocked. [9](#0-8) 
3. **Bad debt risk** — the account is in a zombie state. Any adverse price move that pushes `MAINTENANCE` health below zero creates bad debt that the protocol's insurance fund must absorb, because the account was never liquidated while it was still solvent.

### Likelihood Explanation

The precondition is straightforward: the user needs `INITIAL` health in the range `(0, burnFee]` before the burn. Given that `burnFee = max(ONE, quoteAmount/1000)`, a user with a large NLP position and marginal collateral can trivially satisfy this. No privileged access, no sequencer compromise, and no external dependency is required — only a signed `BurnNlp` transaction through the normal endpoint flow.

### Recommendation

Replace the `MAINTENANCE` health type with `INITIAL` in `burnNlp`, consistent with every other voluntary action:

```solidity
// Before (line 527):
getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,

// After:
getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
``` [1](#0-0) 

### Proof of Concept

1. Deploy the protocol on a local Hardhat fork.
2. Create a subaccount with:
   - A leveraged perp or spot position such that `INITIAL` health ≈ `+0.5 × ONE` and `MAINTENANCE` health ≈ `+5 × ONE`.
   - An NLP balance with `nlpAmount` such that `burnFee = max(ONE, quoteAmount/1000) = ONE` (i.e., a small NLP position).
3. Submit a signed `BurnNlp` transaction for that `nlpAmount`.
4. The `MAINTENANCE >= 0` check passes (health ≈ `+4 × ONE`).
5. Assert: `getHealth(sender, INITIAL) < 0` — confirmed negative.
6. Assert: `getHealth(sender, MAINTENANCE) >= 0` — confirmed non-negative.
7. Attempt to liquidate the account — reverts because `isUnderMaintenance` returns `false`.
8. The account is now in a permanent zombie state until a price move pushes `MAINTENANCE` below zero, at which point bad debt is created.

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

**File:** core/contracts/Clearinghouse.sol (L503-504)
```text
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
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

**File:** core/contracts/Clearinghouse.sol (L541-547)
```text
            require(
                getHealth(
                    nlpPools[i].subaccount,
                    IProductEngine.HealthType.INITIAL
                ) >= 0,
                ERR_SUBACCT_HEALTH
            );
```

**File:** core/contracts/Clearinghouse.sol (L563-566)
```text
        require(
            getHealth(poolSubaccount, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/libraries/RiskHelper.sol (L44-52)
```text
        if (amount >= 0) {
            weight = healthType == IProductEngine.HealthType.INITIAL
                ? risk.longWeightInitialX18
                : risk.longWeightMaintenanceX18;
        } else {
            weight = healthType == IProductEngine.HealthType.INITIAL
                ? risk.shortWeightInitialX18
                : risk.shortWeightMaintenanceX18;
        }
```

**File:** core/contracts/ClearinghouseLiq.sol (L51-58)
```text
    function isUnderMaintenance(bytes32 subaccount) internal returns (bool) {
        // Weighted maintenance health < 0
        return
            getHealthFromClearinghouse(
                subaccount,
                IProductEngine.HealthType.MAINTENANCE
            ) < 0;
    }
```
