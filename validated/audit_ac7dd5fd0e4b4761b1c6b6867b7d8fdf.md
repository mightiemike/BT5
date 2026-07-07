### Title
Unvalidated `sendTo` in `WithdrawCollateralV2` Allows Collateral to Be Permanently Locked in `WithdrawPool` - (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

The `WithdrawCollateralV2` transaction type allows a user to specify an arbitrary `sendTo` address. Neither `EndpointTx.sol` nor `Clearinghouse.withdrawCollateral` validates that `sendTo` is not a protocol contract address (e.g., the `WithdrawPool` itself). A user who sets `sendTo = address(withdrawPool)` will have their collateral transferred into the `WithdrawPool` contract with no user-accessible recovery path, permanently locking the funds.

---

### Finding Description

`WithdrawCollateralV2` is the newer withdrawal path that explicitly exposes a user-controlled `sendTo` field:

```solidity
struct WithdrawCollateralV2 {
    bytes32 sender;
    uint32 productId;
    uint128 amount;
    uint64 nonce;
    address sendTo;       // user-specified recipient
    uint128 appendix;
}
``` [1](#0-0) 

When the sequencer processes this transaction in `EndpointTx.sol`, it passes `signedTx.tx.sendTo` directly to `clearinghouse.withdrawCollateral`:

```solidity
clearinghouse.withdrawCollateral(
    signedTx.tx.sender,
    signedTx.tx.productId,
    signedTx.tx.amount,
    signedTx.tx.sendTo,   // no protocol-address check
    nSubmissions
);
``` [2](#0-1) 

Inside `Clearinghouse.withdrawCollateral`, the only guard on `sendTo` is a zero-address fallback. There is no check that `sendTo` is not a protocol contract:

```solidity
if (sendTo == address(0)) {
    sendTo = address(uint160(bytes20(sender)));
}
handleWithdrawTransfer(token, sendTo, amount, idx);
``` [3](#0-2) 

`handleWithdrawTransfer` first moves tokens from the clearinghouse to the `withdrawPool`, then instructs the pool to forward them to `sendTo`:

```solidity
function handleWithdrawTransfer(IERC20Base token, address to, uint128 amount, uint64 idx) internal virtual {
    token.safeTransfer(withdrawPool, uint256(amount));
    BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
}
``` [4](#0-3) 

`submitWithdrawal` then calls `handleWithdrawTransfer` which executes `token.safeTransfer(sendTo, amount)`:

```solidity
function handleWithdrawTransfer(IERC20Base token, address to, uint128 amount) internal virtual {
    token.safeTransfer(to, uint256(amount));
}
``` [5](#0-4) 

If `sendTo == address(withdrawPool)`, the final `safeTransfer` sends the tokens back into the `WithdrawPool` contract itself. The `WithdrawPool` has no user-accessible function to recover these tokens — the only relevant function is `removeLiquidity`, which is `onlyOwner`:

```solidity
function removeLiquidity(uint32 productId, uint128 amount, address sendTo) external onlyOwner {
    handleWithdrawTransfer(getToken(productId), sendTo, amount);
}
``` [6](#0-5) 

The same outcome occurs if `sendTo == address(clearinghouse)`.

---

### Impact Explanation

A user whose `WithdrawCollateralV2` transaction specifies `sendTo = address(withdrawPool)` (or `address(clearinghouse)`) will:

1. Have their on-chain collateral balance decremented in the `SpotEngine` (the `updateBalance` call at line 412 still executes).
2. Receive zero tokens — the funds are transferred into the `WithdrawPool` contract and are inaccessible to the user.

The corrupted state delta is: user's `SpotEngine` balance is reduced by `amount`, but the user's wallet receives nothing. The tokens accumulate in `WithdrawPool` and can only be recovered by the contract owner via `removeLiquidity`. [7](#0-6) 

---

### Likelihood Explanation

The `WithdrawCollateralV2` path is the actively supported withdrawal type for users who want to redirect funds to a different address (e.g., a cold wallet). Any user interacting with this feature who accidentally pastes the `WithdrawPool` or `Clearinghouse` contract address as `sendTo` — a realistic copy-paste error — will permanently lose their funds. No privileged access or external dependency failure is required; the user's own signed transaction is the trigger. The sequencer processes it without any protocol-level guard. [8](#0-7) 

---

### Recommendation

Add an explicit check in `Clearinghouse.withdrawCollateral` (or in `EndpointTx.sol` before calling the clearinghouse) to reject any `sendTo` that equals a known protocol contract address:

```solidity
require(
    sendTo != address(this) &&
    sendTo != withdrawPool,
    "Invalid sendTo: protocol contract"
);
```

This mirrors the short-term recommendation from the reference report: guard against the contract's own address (and sibling protocol contracts) being set as the recipient. [3](#0-2) 

---

### Proof of Concept

1. Alice holds 1000 USDC of collateral in her Nado subaccount.
2. Alice signs a `WithdrawCollateralV2` transaction with `sendTo = address(withdrawPool)` (e.g., she mistakenly copies the pool address instead of her wallet address). The `sendTo` field is included in the EIP-712 digest, so the signature is valid.
3. The sequencer submits the transaction. `EndpointTx.sol` validates the signature and calls `clearinghouse.withdrawCollateral(..., sendTo=withdrawPool, ...)`.
4. `Clearinghouse.withdrawCollateral` skips the zero-address branch (since `sendTo != address(0)`), calls `handleWithdrawTransfer` which does `token.safeTransfer(withdrawPool, 1000e6)` then `withdrawPool.submitWithdrawal(token, withdrawPool, 1000e6, idx)`.
5. `submitWithdrawal` calls `token.safeTransfer(withdrawPool, 1000e6)` — tokens are sent to the `WithdrawPool` itself.
6. Alice's `SpotEngine` balance is decremented by 1000 USDC. Alice's wallet receives 0 USDC. The 1000 USDC sits in `WithdrawPool` with no user-accessible recovery path. [9](#0-8)

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L97-104)
```text
    struct WithdrawCollateralV2 {
        bytes32 sender;
        uint32 productId;
        uint128 amount;
        uint64 nonce;
        address sendTo;
        uint128 appendix; // Reserved for forward-compatible withdrawal features.
    }
```

**File:** core/contracts/EndpointTx.sol (L442-465)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
            );
            int128 currentFeeX18 = spotEngine
                .getConfig(signedTx.tx.productId)
                .withdrawFeeX18;
            require(signedTx.feeX18 >= 0);
            require(signedTx.feeX18 <= currentFeeX18);
            chargeFee(
                signedTx.tx.sender,
                signedTx.feeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                signedTx.tx.sendTo,
                nSubmissions
            );
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

**File:** core/contracts/Clearinghouse.sol (L410-413)
```text
        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);
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
