### Title
`burnNlp()` Applies MAINTENANCE Health Check Instead of INITIAL, Allowing Users to Deliberately Enter a Non-Liquidatable Under-Margined State — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.burnNlp()` enforces only a MAINTENANCE health check after burning NLP tokens, while every other collateral-reducing operation in the same contract enforces the stricter INITIAL health check. The inline comment explicitly states the check "prevents malicious actors from deliberately creating unhealthy subaccounts through NLP burns," yet the weaker MAINTENANCE threshold allows INITIAL health to go negative. This is a direct specification mismatch: a health-check type is applied unconditionally to all NLP burns when the protocol's own stated intent and the behavior of every analogous operation require INITIAL health enforcement.

---

### Finding Description

`Clearinghouse.burnNlp()` ends with:

```solidity
// Burning NLP can decrease health if the burn fee exceeds the health improvement
// from the withdrawal. This check prevents malicious actors from deliberately
// creating unhealthy subaccounts through NLP burns.
require(
    getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
    ERR_SUBACCT_HEALTH
);
``` [1](#0-0) 

Every other collateral-reducing path in the same contract uses INITIAL:

- `withdrawCollateral()` derives `healthType = INITIAL` for all non-`X_ACCOUNT` senders and requires `getHealth(sender, healthType) >= 0`. [2](#0-1) 

- `mintNlp()` requires `getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0`. [3](#0-2) 

- `transferQuote()` calls `_isAboveInitial(txn.sender)` which internally uses INITIAL. [4](#0-3) 

MAINTENANCE health is the liquidation threshold; INITIAL health is the margin-adequacy threshold. The gap between them is the protocol's intentional risk buffer. By using MAINTENANCE in `burnNlp()`, the function allows a user to exit the INITIAL-healthy zone without triggering liquidation, placing them in a state the protocol's own comment says it is trying to prevent.

The burn fee (`max(ONE, quoteAmount / 1000)`) is deducted from the quote the user receives: [5](#0-4) 

If the user's INITIAL health is close to zero before the burn, the fee-induced net health loss can push INITIAL health negative while MAINTENANCE health remains positive.

---

### Impact Explanation

After a successful `burnNlp()` call that leaves INITIAL health < 0 but MAINTENANCE health ≥ 0, the subaccount is in a "gray zone":

| Operation | Requires | Result in gray zone |
|---|---|---|
| `withdrawCollateral` | INITIAL ≥ 0 | **Blocked** |
| `transferQuote` | INITIAL ≥ 0 | **Blocked** |
| `mintNlp` | INITIAL ≥ 0 | **Blocked** |
| `liquidateSubaccountImpl` | MAINTENANCE < 0 | **Blocked** |
| `matchOrders` | `isHealthy()` → always `true` | **Allowed** | [6](#0-5) 

The user has exceeded their initial margin requirements but cannot be liquidated. They can continue trading, potentially deepening their under-margined position. The protocol's collateral accounting is corrupted: INITIAL health is negative (signaling over-leverage) but no protocol mechanism can force position reduction until MAINTENANCE also goes negative. This is the direct analog to the external report's CIF inflation: a metric (INITIAL health) is allowed to go negative unconditionally across all NLP burns when the protocol's own specification requires it to stay non-negative.

---

### Likelihood Explanation

Medium. The trigger requires a user to:
1. Hold unlocked NLP tokens (subject to the 4-day lock period enforced in `SpotEngine.handleNlpLockedBalance`).
2. Have other open positions such that INITIAL health is slightly positive before the burn.
3. Submit a `BurnNlp` transaction sized so the burn fee pushes INITIAL health below zero while MAINTENANCE stays non-negative.

This is a deliberate, user-controlled action reachable through the standard `BurnNlp` transaction type submitted via the `Endpoint`. No privileged access is required. The sequencer processes the transaction and calls `Clearinghouse.burnNlp()` on-chain; the MAINTENANCE-only check is the sole on-chain gate.

<cite repo="patrichyt/nado-contracts--005" path="core/contracts/Clearinghouse.sol" start="485" end="491

### Citations

**File:** core/contracts/Clearinghouse.sol (L249-249)
```text
        require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
```

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

**File:** core/contracts/OffchainExchange.sol (L625-629)
```text
    function isHealthy(
        bytes32 /* subaccount */
    ) internal view virtual returns (bool) {
        return true;
    }
```
