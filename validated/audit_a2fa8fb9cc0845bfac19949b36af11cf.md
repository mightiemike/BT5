### Title
Sanctioned Users Cannot Submit Slow Mode Withdrawal, Permanently Locking Deposited Funds — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

`submitSlowModeTransactionImpl` applies `requireUnsanctioned(sender)` to **all** slow mode transaction types, including `WithdrawCollateral`. Because deposits also require the caller to be unsanctioned, the sanctions check is applied symmetrically to both the "add" (deposit) and "remove" (withdrawal submission) operations. A user who deposits funds and is later sanctioned by the external oracle cannot use the slow mode path — their only self-custody fallback — to recover their funds.

---

### Finding Description

In `Endpoint.sol`, `depositCollateralWithReferral` enforces:

```solidity
requireUnsanctioned(msg.sender);
requireUnsanctioned(sender);
``` [1](#0-0) 

In `EndpointTx.sol`, `submitSlowModeTransactionImpl` enforces the same check unconditionally for every slow mode transaction type before queuing it:

```solidity
requireUnsanctioned(sender);
slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({...});
``` [2](#0-1) 

This means `WithdrawCollateral` submitted via slow mode is blocked for sanctioned users, just as deposits are. The slow mode path is the protocol's self-custody escape hatch — it exists precisely for situations where the sequencer is unresponsive or censoring. The sequencer path for `WithdrawCollateral` does not check sanctions:

```solidity
} else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
    ...
    validateSignedTx(...);
    chargeFee(...);
    clearinghouse.withdrawCollateral(...);
}
``` [3](#0-2) 

There is no sanctions check in `clearinghouse.withdrawCollateral` either: [4](#0-3) 

So the only path available to a sanctioned user is the sequencer — a centralized entity that is expected to refuse processing for sanctioned addresses.

---

### Impact Explanation

A user deposits collateral while unsanctioned. They are later added to the Chainalysis sanctions list. At that point:

- They cannot call `submitSlowModeTransaction` with a `WithdrawCollateral` payload — `requireUnsanctioned` reverts.
- They are entirely dependent on the sequencer to include their withdrawal transaction.
- The sequencer, operating under compliance obligations, will refuse to process transactions for sanctioned addresses.
- The user's deposited collateral is permanently locked in the protocol with no on-chain self-custody recovery path.

The corrupted state is: `spotEngine.balances[productId][subaccount]` holds a positive balance that the owner can never unilaterally reclaim.

---

### Likelihood Explanation

Sanctions lists are dynamic. Users can be added to the OFAC/Chainalysis list after having already deposited funds. This is not a theoretical edge case — it has occurred in practice across multiple DeFi protocols. The trigger requires no privileged access: the external oracle update is the only prerequisite, and the Nado code's symmetric sanctions check is the necessary vulnerable step.

---

### Recommendation

Remove the `requireUnsanctioned` check from the slow mode submission path specifically for `WithdrawCollateral` (and `WithdrawCollateralV2`). Withdrawal of one's own funds should not be gated by sanctions status — the sanctions check is appropriate for deposits (preventing new funds from entering) but should not prevent users from reclaiming funds already in the system. A targeted fix:

```solidity
// Only check sanctions for non-withdrawal slow mode txs
if (
    txType != IEndpoint.TransactionType.WithdrawCollateral &&
    txType != IEndpoint.TransactionType.WithdrawCollateralV2
) {
    requireUnsanctioned(sender);
}
```

---

### Proof of Concept

1. Alice deposits 10,000 USDC via `depositCollateralWithReferral` while unsanctioned. Her balance is recorded in `spotEngine`.
2. Alice is added to the Chainalysis sanctions list.
3. Alice calls `submitSlowModeTransaction` with a `WithdrawCollateral` payload.
4. `submitSlowModeTransactionImpl` calls `requireUnsanctioned(msg.sender)` → reverts because Alice is sanctioned.
5. Alice cannot submit any slow mode withdrawal. The sequencer refuses to include her withdrawal in the fast path.
6. Alice's 10,000 USDC remains permanently locked in the clearinghouse with no on-chain recovery path.

### Citations

**File:** core/contracts/Endpoint.sol (L134-135)
```text
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);
```

**File:** core/contracts/EndpointTx.sol (L375-380)
```text
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
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

**File:** core/contracts/Clearinghouse.sol (L391-421)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }
```
