### Title
Fast Withdrawal Fees Permanently Stranded in `WithdrawPool` With No Claim Mechanism — (`File: core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`BaseWithdrawPool.submitFastWithdrawal` accumulates real ERC-20 fee tokens into the contract and records them in `fees[productId]`, but there is no dedicated function to route these fees back to the protocol treasury or credit them to the protocol's internal accounting system (`X_ACCOUNT` in `SpotEngine`). The fees are permanently stranded in `WithdrawPool` with no proper claim path.

---

### Finding Description

In `BaseWithdrawPool.submitFastWithdrawal`, a fee is computed and retained in the contract on every fast withdrawal:

- **Case 1** (`sendTo == msg.sender`): The fee is deducted from `transferAmount`, so the fee tokens remain in the contract from the batch of tokens previously sent by `Clearinghouse`.
- **Case 2** (`sendTo != msg.sender`): The fee is explicitly transferred *from the caller into the contract* via `safeTransferFrom`.

In both cases, `fees[productId] += fee` records the accumulation, and the fee tokens sit in the `WithdrawPool` contract. [1](#0-0) 

The `fees` mapping is `public` (readable) but is **never decremented**. There is no `claimFees`, `sweepFees`, or equivalent function anywhere in `BaseWithdrawPool` or `WithdrawPool` that would:

1. Transfer accumulated fee tokens to the protocol treasury or `X_ACCOUNT`.
2. Credit them to `SpotEngine` internal balances (as `claimSequencerFees` does for engine-level fees).
3. Decrement `fees[productId]` to maintain accounting integrity. [2](#0-1) 

The only extraction path is `removeLiquidity`, an `onlyOwner` blunt-instrument function that transfers raw token balances without updating `fees[productId]` or any `SpotEngine` state: [3](#0-2) 

By contrast, `Clearinghouse.claimSequencerFees` demonstrates the correct pattern for routing protocol fees back into the accounting system — but it only covers `SpotEngine`/`PerpEngine` fee balances, not `WithdrawPool` fees: [4](#0-3) 

---

### Impact Explanation

**Medium.** Fast withdrawal fees are real ERC-20 tokens that accumulate in `WithdrawPool` and represent protocol revenue. Without a dedicated claim mechanism:

- Fee revenue is permanently stranded in `WithdrawPool` with no proper routing to the protocol treasury or `X_ACCOUNT`.
- The `fees` mapping diverges from actual token accounting over time (it is never decremented), creating a persistent accounting desynchronization.
- If the owner uses `removeLiquidity` to extract tokens, it does not decrement `fees[productId]`, leaving the mapping permanently inflated and misleading. Worse, `removeLiquidity` does not distinguish fee surplus from user liquidity, so an over-extraction could leave the pool unable to service future sequencer-submitted withdrawals via `submitWithdrawal`. [5](#0-4) 

---

### Likelihood Explanation

**High.** `submitFastWithdrawal` is a public, permissionless function callable by any liquidity provider. Every successful fast withdrawal generates a fee. The fee accumulation is continuous and automatic; no special conditions are required to trigger it. [6](#0-5) 

---

### Recommendation

Add a `claimFees(uint32 productId, address recipient)` function (restricted to `onlyOwner` or a designated fee recipient) that:

1. Reads `fees[productId]`.
2. Resets `fees[productId] = 0`.
3. Transfers the corresponding token amount to the protocol treasury or calls back into `Clearinghouse` to credit `X_ACCOUNT` in `SpotEngine`, mirroring the pattern used in `claimSequencerFees`.

---

### Proof of Concept

1. A fast withdrawal provider calls `submitFastWithdrawal` with `sendTo != msg.sender`.
2. `safeTransferFrom(token, msg.sender, uint128(fee))` pulls fee tokens into `WithdrawPool`.
3. `fees[productId] += fee` records the accumulation.
4. No function exists to route these tokens to the protocol. `fees[productId]` grows indefinitely.
5. The owner can call `removeLiquidity(productId, amount, sendTo)` to extract tokens, but `fees[productId]` is never decremented, and the extraction is indistinguishable from withdrawing user liquidity — creating a risk of under-collateralizing the pool for future sequencer withdrawals. [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L40-41)
```text
    mapping(uint32 => int128) public fees;

```

**File:** core/contracts/BaseWithdrawPool.sol (L81-114)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;

        Verifier v = Verifier(verifier);
        v.requireValidTxSignatures(transaction, idx, signatures);

        (
            uint32 productId,
            address sendTo,
            uint128 transferAmount
        ) = resolveFastWithdrawal(transaction);
        IERC20Base token = getToken(productId);

        require(transferAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);

        int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L116-132)
```text
    function submitWithdrawal(
        IERC20Base token,
        address sendTo,
        uint128 amount,
        uint64 idx
    ) public {
        require(msg.sender == clearinghouse);

        if (markedIdxs[idx]) {
            return;
        }
        markedIdxs[idx] = true;
        // set minIdx to most recent withdrawal submitted by sequencer
        minIdx = idx;

        handleWithdrawTransfer(token, sendTo, amount);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L151-157)
```text
    function removeLiquidity(
        uint32 productId,
        uint128 amount,
        address sendTo
    ) external onlyOwner {
        handleWithdrawTransfer(getToken(productId), sendTo, amount);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L184-198)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount
    ) internal virtual {
        token.safeTransfer(to, uint256(amount));
    }

    function safeTransferFrom(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal virtual {
        token.safeTransferFrom(from, address(this), amount);
    }
```

**File:** core/contracts/Clearinghouse.sol (L569-615)
```text
    function claimSequencerFees(int128[] calldata fees)
        external
        virtual
        onlyEndpoint
    {
        ISpotEngine spotEngine = _spotEngine();
        IPerpEngine perpEngine = _perpEngine();

        uint32[] memory spotIds = spotEngine.getProductIds();
        uint32[] memory perpIds = perpEngine.getProductIds();

        for (uint256 i = 0; i < spotIds.length; i++) {
            ISpotEngine.Balance memory feeBalance = spotEngine.getBalance(
                spotIds[i],
                FEES_ACCOUNT
            );
            spotEngine.updateBalance(
                spotIds[i],
                X_ACCOUNT,
                fees[i] + feeBalance.amount
            );
            spotEngine.updateBalance(
                spotIds[i],
                FEES_ACCOUNT,
                -feeBalance.amount
            );
        }

        for (uint256 i = 0; i < perpIds.length; i++) {
            IPerpEngine.Balance memory feeBalance = perpEngine.getBalance(
                perpIds[i],
                FEES_ACCOUNT
            );
            perpEngine.updateBalance(
                perpIds[i],
                X_ACCOUNT,
                feeBalance.amount,
                feeBalance.vQuoteBalance
            );
            perpEngine.updateBalance(
                perpIds[i],
                FEES_ACCOUNT,
                -feeBalance.amount,
                -feeBalance.vQuoteBalance
            );
        }
    }
```
