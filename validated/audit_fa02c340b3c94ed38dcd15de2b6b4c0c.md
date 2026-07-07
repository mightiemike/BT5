### Title
Slow Mode `WithdrawCollateral` Bypasses `withdrawFeeX18` Fee, Incentivizing Deliberate Delay — (File: `core/contracts/EndpointTx.sol`)

### Summary
The `processSlowModeTransactionImpl` function handles `WithdrawCollateral` transactions without charging the product-specific `withdrawFeeX18` fee. The fast-path handler (`processTransactionImpl`) always charges this fee. Any user can deliberately route their withdrawal through slow mode to avoid the fee entirely, paying only the flat `SLOW_MODE_FEE` ($1) instead. This is a direct analog to the reported pattern: a time-delayed action path that avoids a fee that should apply, creating a rational incentive to always use the delayed path.

### Finding Description

In `processTransactionImpl`, a `WithdrawCollateral` transaction charges the product-specific withdrawal fee before executing:

```solidity
// EndpointTx.sol ~L425-436
chargeFee(
    signedTx.tx.sender,
    spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
    signedTx.tx.productId
);
clearinghouse.withdrawCollateral(
    signedTx.tx.sender,
    signedTx.tx.productId,
    signedTx.tx.amount,
    address(0),
    nSubmissions
);
```

In `processSlowModeTransactionImpl`, the same `WithdrawCollateral` transaction type is handled with **no fee charge**:

```solidity
// EndpointTx.sol ~L217-229
} else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
    IEndpoint.WithdrawCollateral memory txn = abi.decode(
        transaction[1:],
        (IEndpoint.WithdrawCollateral)
    );
    validateSender(txn.sender, sender);
    clearinghouse.withdrawCollateral(
        txn.sender,
        txn.productId,
        txn.amount,
        address(0),
        nSubmissions
    );
```

The slow mode submission path (`submitSlowModeTransactionImpl`) charges only the flat `SLOW_MODE_FEE = 1000000` ($1 in 6-decimal USDC) from the user's wallet, which is unrelated to the product-specific `withdrawFeeX18`. The `withdrawFeeX18` is charged in the product token from the user's on-chain balance — a distinct asset and accounting path. By submitting via slow mode, the user pays $1 in quote token from their wallet and avoids paying `withdrawFeeX18` in the product token from their protocol balance entirely.

The `WithdrawCollateral` type is not restricted to owner-only in `submitSlowModeTransactionImpl` (it falls into the `else` branch), so any user can submit it. After the `SLOW_MODE_TX_DELAY` of 3 days, the transaction becomes executable by anyone via `executeSlowModeTransaction()`.

### Impact Explanation

Users systematically avoid paying `withdrawFeeX18` on every withdrawal. The `withdrawFeeX18` is also used as the basis for the fast-withdrawal minimum fee (`5 * withdrawFeeX18`), meaning it is a meaningful protocol revenue parameter. Protocol fee revenue from withdrawals is permanently reduced to zero for any user who adopts this pattern. The `fastWithdrawalFeeAmount` function in `BaseWithdrawPool.sol` uses `withdrawFeeX18` as a floor, confirming it is intended to be a real, non-trivial fee.

### Likelihood Explanation

The attack requires no special privileges — any user with a balance can submit a `WithdrawCollateral` via `submitSlowModeTransaction()`. The only cost is a 3-day wait and a flat $1 slow mode fee. For any withdrawal where `withdrawFeeX18 > $1` (in dollar terms), the user strictly profits by using slow mode. Since the slow mode path is always available and the sequencer cannot block it (that is the design intent of slow mode), this is a reliable, repeatable bypass with no counterplay.

### Recommendation

Charge `withdrawFeeX18` inside `processSlowModeTransactionImpl` for `WithdrawCollateral` transactions, mirroring the fast-path logic:

```solidity
} else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
    IEndpoint.WithdrawCollateral memory txn = abi.decode(
        transaction[1:],
        (IEndpoint.WithdrawCollateral)
    );
    validateSender(txn.sender, sender);
    // Add fee charge to match fast-path behavior:
    // chargeFee(txn.sender, spotEngine.getConfig(txn.productId).withdrawFeeX18, txn.productId);
    clearinghouse.withdrawCollateral(...);
```

Alternatively, document explicitly that slow mode withdrawals are fee-exempt (e.g., as a censorship-resistance concession), and ensure `withdrawFeeX18` values are set with this bypass in mind.

### Proof of Concept

1. User holds a balance in product `P` with `withdrawFeeX18 = X` (where `X > SLOW_MODE_FEE` in dollar terms).
2. User calls `Endpoint.submitSlowModeTransaction(abi.encodePacked(uint8(TransactionType.WithdrawCollateral), abi.encode(WithdrawCollateral({sender: subaccount, productId: P, amount: A}))))`.
3. User pays `SLOW_MODE_FEE = $1` from their wallet. No `withdrawFeeX18` is deducted.
4. After 3 days, anyone calls `Endpoint.executeSlowModeTransaction()`.
5. `processSlowModeTransactionImpl` executes `clearinghouse.withdrawCollateral(...)` with no fee charge.
6. User receives full withdrawal amount `A` without paying `withdrawFeeX18 = X`.
7. Net saving per withdrawal: `X - $1`. Repeatable indefinitely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/EndpointTx.sol (L217-229)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.WithdrawCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.WithdrawCollateral)
            );
            validateSender(txn.sender, sender);
            clearinghouse.withdrawCollateral(
                txn.sender,
                txn.productId,
                txn.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/EndpointTx.sol (L413-436)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/common/Constants.sol (L23-25)
```text
int128 constant SLOW_MODE_FEE = 1000000; // $1

int128 constant FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000; // 0.1%
```

**File:** core/contracts/BaseWithdrawPool.sol (L134-149)
```text
    function fastWithdrawalFeeAmount(
        IERC20Base token,
        uint32 productId,
        uint128 amount
    ) public view returns (int128) {
        uint8 decimals = token.decimals();
        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));
        int128 amountX18 = int128(amount) * int128(multiplier);

        int128 proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18);
        int128 minFeeX18 = 5 * spotEngine().getConfig(productId).withdrawFeeX18;

        int128 feeX18 = MathHelper.max(proportionalFeeX18, minFeeX18);
        return feeX18 / int128(multiplier);
    }
```

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```
