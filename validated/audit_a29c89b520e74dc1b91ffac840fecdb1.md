### Title
Blacklisted Recipient Permanently Locks User Collateral in Withdrawal Path — (`core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`BaseWithdrawPool.handleWithdrawTransfer` calls `token.safeTransfer(to, amount)` with no fallback. If the recipient address `to` is blacklisted by a token such as USDC or USDT, the transfer reverts, the entire withdrawal transaction reverts, and the user's collateral is permanently locked in the protocol with no alternative claim path.

---

### Finding Description

The withdrawal pipeline in Nado routes all token disbursements through `BaseWithdrawPool.handleWithdrawTransfer`: [1](#0-0) 

```solidity
function handleWithdrawTransfer(
    IERC20Base token,
    address to,
    uint128 amount
) internal virtual {
    token.safeTransfer(to, uint256(amount));
}
```

`ERC20Helper.safeTransfer` wraps the call with a hard `require`: [2](#0-1) 

```solidity
function safeTransfer(IERC20Base self, address to, uint256 amount) internal {
    (bool success, bytes memory data) = address(self).call(...);
    require(
        success && (data.length == 0 || abi.decode(data, (bool))),
        ERR_TRANSFER_FAILED
    );
}
```

`handleWithdrawTransfer` is invoked from two public entry points:

**Slow withdrawal path** — `Clearinghouse.withdrawCollateral` → `Clearinghouse.handleWithdrawTransfer` → `BaseWithdrawPool.submitWithdrawal` → `handleWithdrawTransfer`: [3](#0-2) 

**Fast withdrawal path** — `BaseWithdrawPool.submitFastWithdrawal` → `handleWithdrawTransfer`: [4](#0-3) 

In both paths, if `to` is blacklisted by the underlying token (e.g., USDC), `safeTransfer` reverts, the entire transaction reverts, and no state is mutated. The user's balance in `SpotEngine` is never decremented, meaning the funds remain credited on-chain but are permanently unwithdrawable. There is no try-catch, no `claims[user]` accumulator, and no alternative disbursement path.

The `Clearinghouse.withdrawCollateral` function also shows that `sendTo` defaults to the sender's own address when unspecified: [5](#0-4) 

While `WithdrawCollateralV2` allows a custom `sendTo`, this requires the user to proactively sign a new transaction routed through the sequencer — there is no on-chain self-service escape hatch if the sequencer does not cooperate or if the user is unaware of the blacklist.

---

### Impact Explanation

A user whose address is added to a token blacklist (e.g., USDC's `blacklist` mapping) after depositing collateral into Nado will find every withdrawal attempt permanently reverting. Their collateral balance remains credited in `SpotEngine` but is irrecoverable. There is no on-chain claim mechanism, no admin rescue path, and no protocol-level fallback. Funds are effectively frozen indefinitely.

---

### Likelihood Explanation

USDC and USDT both implement address blacklists enforced at the token level. A user can be blacklisted at any time after depositing — due to regulatory action, sanctions compliance, or exchange-reported fraud. The scenario (deposit → blacklist → attempt withdrawal) is realistic and has occurred on other protocols. The protocol supports USDC as a quote token, making this directly applicable.

---

### Recommendation

Wrap the `safeTransfer` in `handleWithdrawTransfer` in a try-catch pattern and, on failure, credit the amount to a `pendingWithdrawals[token][recipient]` mapping. Provide a separate `claimPendingWithdrawal(address token)` function that the user (or any address they designate) can call to retry the transfer. This mirrors the `claims[token]` pattern recommended in the external report and avoids the protocol facilitating blacklist circumvention, since the recipient remains the original user.

---

### Proof of Concept

1. User deposits 10,000 USDC into Nado via `Endpoint.depositCollateral`. Balance is credited in `SpotEngine`.
2. User's address is added to USDC's blacklist (e.g., OFAC sanction).
3. User (or sequencer on their behalf) calls `withdrawCollateral` → `Clearinghouse.handleWithdrawTransfer` → `BaseWithdrawPool.submitWithdrawal` → `BaseWithdrawPool.handleWithdrawTransfer` → `token.safeTransfer(userAddress, 10000e6)`.
4. USDC's `transfer` returns `false` for a blacklisted recipient; `ERC20Helper.safeTransfer` reverts with `ERR_TRANSFER_FAILED`.
5. The entire transaction reverts. `SpotEngine` balance is unchanged. `markedIdxs[idx]` is not set.
6. Every subsequent retry produces the same revert. The user's 10,000 USDC is permanently locked. [1](#0-0) [6](#0-5) [7](#0-6)

### Citations

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

**File:** core/contracts/BaseWithdrawPool.sol (L184-190)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount
    ) internal virtual {
        token.safeTransfer(to, uint256(amount));
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L9-21)
```text
    function safeTransfer(
        IERC20Base self,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

**File:** core/contracts/Clearinghouse.sol (L377-385)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount,
        uint64 idx
    ) internal virtual {
        token.safeTransfer(withdrawPool, uint256(amount));
        BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
    }
```

**File:** core/contracts/Clearinghouse.sol (L404-408)
```text
        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);
```
