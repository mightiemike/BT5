### Title
`_applyNlpRebalance` Applies Quote Balance Updates to Deleted NLP Pools Without Validity Check — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

`_applyNlpRebalance` iterates over the full `nlpPools` array and applies quote balance deltas to every pool subaccount, including pools that have been deleted (`owner == address(0)`, `balanceWeightX18 == 0`). The upstream `_validateNlpRebalance` guard only verifies that the sum of rebalance amounts equals the expected quote delta; it never checks whether each individual target pool is still active. A user-signed `MintNlp` or `BurnNlp` transaction can therefore direct quote funds into a deleted pool's subaccount, where they become inaccessible through normal protocol paths.

---

### Finding Description

When a pool is removed via `deleteNlpPool`, the implementation calls `updateNlpPool(poolId, address(0), uint128(0))`, zeroing the owner and weight but **leaving the pool entry in the `nlpPools` array** with its subaccount address intact. [1](#0-0) 

The `_validateNlpRebalance` function enforces only two invariants:

1. `nlpPools.length == nlpPoolRebalanceX18.length`
2. The arithmetic sum of all rebalance amounts equals `deltaQuoteAmount` [2](#0-1) 

There is **no per-pool check** that `nlpPools[i].balanceWeightX18 > 0` or `nlpPools[i].owner != address(0)` before the balance update is applied.

`_applyNlpRebalance` then unconditionally calls `spotEngine.updateBalance` for every index, including deleted pools: [3](#0-2) 

This is invoked from both `mintNlp` and `burnNlp`: [4](#0-3) [5](#0-4) 

A user signs a `SignedMintNlp` (or `SignedBurnNlp`) transaction whose body includes `nlpPoolRebalanceX18`. The EIP-712 digest is computed over the full transaction bytes, so the user controls these values: [6](#0-5) 

The user can set `nlpPoolRebalanceX18[deletedPoolIndex]` to a positive value while keeping the sum equal to `quoteAmount` (by reducing another active pool's share). The on-chain validation passes, and `_applyNlpRebalance` credits the deleted pool's subaccount.

---

### Impact Explanation

Quote tokens credited to a deleted pool's subaccount are effectively locked:

- `NlpProfitShare` explicitly rejects deleted pools: `require(nlpPools[txn.poolId].owner != address(0), ERR_INVALID_NLP_POOL)` — so the funds cannot be extracted via the normal profit-share path. [7](#0-6) 

- Recovery requires an admin to re-invoke `DeleteNlpPool` on the already-deleted pool, which re-triggers `clearNlpPoolPosition` and sweeps the balance to `N_ACCOUNT`. [8](#0-7) 

Until that admin action occurs, the quote funds are stranded in a subaccount with no authorized owner, directly analogous to emissions being lost in the reference report.

---

### Likelihood Explanation

The sequencer submits `MintNlp`/`BurnNlp` transactions, but the user controls and signs `nlpPoolRebalanceX18`. The on-chain code is the authoritative validation layer; it contains no per-pool activity check. Any scenario where the sequencer does not independently re-validate individual pool weights before submission — including a misconfigured sequencer, a sequencer upgrade that misses this edge case, or a future sequencer that trusts the on-chain validation as sufficient — allows the misdirection. The missing guard is a structural omission, not a configuration issue.

---

### Recommendation

Add an active-pool guard inside `_validateNlpRebalance` (or at the top of `_applyNlpRebalance`) that skips or rejects non-zero rebalance amounts targeting deleted pools:

```solidity
for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
    if (nlpPoolRebalanceX18[i] != 0) {
        require(
            nlpPools[i].balanceWeightX18 > 0,   // pool must be active
            ERR_INVALID_NLP_POOL
        );
    }
    rebalanceAmount += nlpPoolRebalanceX18[i];
}
```

This mirrors the fix applied in the reference report: skip (or revert on) operations targeting entities that are no longer valid.

---

### Proof of Concept

1. Protocol has two pools: pool 0 (active) and pool 1 (active, `owner = Alice`).
2. Admin calls `DeleteNlpPool(1)` → pool 1 entry remains in `nlpPools` with `owner = address(0)`, `balanceWeightX18 = 0`, subaccount `S1` intact.
3. User crafts a `SignedMintNlp` with `quoteAmount = 1000` and `nlpPoolRebalanceX18 = [500, 500]` (500 to pool 0, 500 to deleted pool 1).
4. `_validateNlpRebalance`: `500 + 500 == 1000` ✓, lengths match ✓ — passes.
5. `_applyNlpRebalance`: credits 500 USDC to pool 0 subaccount and 500 USDC to `S1` (deleted pool 1 subaccount).
6. `NlpProfitShare(poolId=1, ...)` reverts: `nlpPools[1].owner == address(0)`.
7. 500 USDC is stranded in `S1` until admin re-runs `DeleteNlpPool(1)` to sweep it to `N_ACCOUNT`. [3](#0-2) [1](#0-0)

### Citations

**File:** core/contracts/EndpointTx.sol (L66-70)
```text
    function deleteNlpPool(uint64 poolId) private {
        require(poolId > 0 && poolId < nlpPools.length);
        clearinghouse.clearNlpPoolPosition(nlpPools[poolId].subaccount);
        updateNlpPool(poolId, address(0), uint128(0));
    }
```

**File:** core/contracts/EndpointTx.sol (L294-313)
```text
            require(
                txn.poolId > 0 && txn.poolId < nlpPools.length,
                ERR_INVALID_NLP_POOL
            );
            require(
                nlpPools[txn.poolId].owner != address(0),
                ERR_INVALID_NLP_POOL
            );
            require(
                address(uint160(bytes20(txn.recipient))) ==
                    nlpPools[txn.poolId].owner,
                ERR_UNAUTHORIZED
            );
            requireSubaccount(txn.recipient);
            require(!RiskHelper.isIsolatedSubaccount(txn.recipient));
            clearinghouse.nlpProfitShare(
                nlpPools[txn.poolId].subaccount,
                txn.recipient,
                txn.amount
            );
```

**File:** core/contracts/EndpointTx.sol (L534-553)
```text
        } else if (txType == IEndpoint.TransactionType.MintNlp) {
            IEndpoint.SignedMintNlp memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedMintNlp)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.mintNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
```

**File:** core/contracts/Clearinghouse.sol (L423-437)
```text
    function _validateNlpRebalance(
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18,
        int128 deltaQuoteAmount
    ) internal pure {
        require(
            nlpPools.length == nlpPoolRebalanceX18.length,
            ERR_INVALID_NLP_REBALANCE
        );
        int128 rebalanceAmount = 0;
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            rebalanceAmount += nlpPoolRebalanceX18[i];
        }
        require(deltaQuoteAmount == rebalanceAmount, ERR_INVALID_NLP_REBALANCE);
    }
```

**File:** core/contracts/Clearinghouse.sol (L439-451)
```text
    function _applyNlpRebalance(
        ISpotEngine spotEngine,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) internal {
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                nlpPools[i].subaccount,
                nlpPoolRebalanceX18[i]
            );
        }
    }
```

**File:** core/contracts/Clearinghouse.sol (L468-477)
```text
        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] >= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
        _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
```

**File:** core/contracts/Clearinghouse.sol (L506-516)
```text
        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, -quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] <= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);

        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
```

**File:** core/contracts/Clearinghouse.sol (L768-812)
```text
    function clearNlpPoolPosition(bytes32 subaccount)
        external
        virtual
        onlyEndpoint
    {
        require(subaccount != N_ACCOUNT, ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        uint32[] memory spotProducts = spotEngine.getProductIds();

        IPerpEngine perpEngine = _perpEngine();
        uint32[] memory perpProducts = perpEngine.getProductIds();

        for (uint32 i = 0; i < spotProducts.length; i++) {
            uint32 productId = spotProducts[i];

            ISpotEngine.Balance memory balance = spotEngine.getBalance(
                productId,
                subaccount
            );
            spotEngine.updateBalance(productId, subaccount, -balance.amount);
            spotEngine.updateBalance(productId, N_ACCOUNT, balance.amount);
        }

        for (uint32 i = 0; i < perpProducts.length; i++) {
            uint32 productId = perpProducts[i];

            IPerpEngine.Balance memory balance = perpEngine.getBalance(
                productId,
                subaccount
            );
            perpEngine.updateBalance(
                productId,
                subaccount,
                -balance.amount,
                -balance.vQuoteBalance
            );
            perpEngine.updateBalance(
                productId,
                N_ACCOUNT,
                balance.amount,
                balance.vQuoteBalance
            );
        }
    }
```
