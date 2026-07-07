### Title
Sanctioned User Can Execute Queued `WithdrawCollateral` via `executeSlowModeTransaction` After Being Sanctioned — (`core/contracts/Endpoint.sol`)

---

### Summary

`executeSlowModeTransaction()` is callable by anyone and processes queued slow-mode transactions without re-checking the sender's sanctions status at execution time. A user who was unsanctioned at submission but becomes sanctioned during the 3-day delay can still have their `WithdrawCollateral` slow-mode transaction executed, transferring funds to a sanctioned address.

---

### Finding Description

The sanctions check in the slow-mode flow is performed only once — at **submission time** — inside `submitSlowModeTransactionImpl`: [1](#0-0) 

```solidity
requireUnsanctioned(sender);
slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({...});
```

After the 3-day `SLOW_MODE_TX_DELAY`, **anyone** can call `executeSlowModeTransaction()`: [2](#0-1) 

This calls `_executeSlowModeTransaction` → `processSlowModeTransaction` → `processSlowModeTransactionImpl`. The `WithdrawCollateral` branch in `processSlowModeTransactionImpl` performs no sanctions check: [3](#0-2) 

```solidity
} else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
    IEndpoint.WithdrawCollateral memory txn = abi.decode(...);
    validateSender(txn.sender, sender);
    clearinghouse.withdrawCollateral(
        txn.sender, txn.productId, txn.amount, address(0), nSubmissions
    );
```

`Clearinghouse.withdrawCollateral` also contains no sanctions check — it only validates health and isolated-subaccount status: [4](#0-3) 

The `sendTo` address defaults to `address(uint160(bytes20(sender)))` — the sanctioned user's wallet — and funds are transferred unconditionally via `handleWithdrawTransfer`. [5](#0-4) 

---

### Impact Explanation

A sanctioned user can receive collateral withdrawals from the protocol despite being on the sanctions list. This directly violates the protocol's compliance invariant (enforced by `requireUnsanctioned`) and transfers real ERC-20 assets to a sanctioned address. The corrupted state delta is: `spotEngine` balance decremented for the subaccount, and ERC-20 tokens transferred to a sanctioned wallet via `WithdrawPool`.

---

### Likelihood Explanation

The 3-day slow-mode delay window creates a realistic race condition: a user submits a withdrawal, gets sanctioned (e.g., OFAC designation), and the queued transaction executes after the delay. The trigger (`executeSlowModeTransaction`) requires no privilege — any EOA can call it. The `LinkSigner` slow-mode path has the same missing check and allows a sanctioned user to establish a linked signer relationship post-sanction, though with lower direct asset impact.

---

### Recommendation

Re-check sanctions status at **execution time** inside `_executeSlowModeTransaction` or `processSlowModeTransactionImpl`. For `WithdrawCollateral`, add `requireUnsanctioned(address(uint160(bytes20(txn.sender))))` before calling `clearinghouse.withdrawCollateral`. Alternatively, add a centralized sanctions re-check at the top of `processSlowModeTransactionImpl` for all transaction types that move funds or mutate signer state.

---

### Proof of Concept

1. User calls `submitSlowModeTransaction` with a `WithdrawCollateral` transaction. Passes `requireUnsanctioned` at `EndpointTx.sol:375`.
2. User is added to the sanctions list (e.g., Chainalysis oracle updated).
3. After `SLOW_MODE_TX_DELAY` (3 days), any third party calls `Endpoint.executeSlowModeTransaction()`.
4. Execution path: `_executeSlowModeTransaction` → `processSlowModeTransaction` → `processSlowModeTransactionImpl` (WithdrawCollateral branch, `EndpointTx.sol:217-229`) → `Clearinghouse.withdrawCollateral` (`Clearinghouse.sol:391-421`).
5. No sanctions check occurs. `handleWithdrawTransfer` sends tokens to the sanctioned address via `WithdrawPool`.
6. The sanctioned user receives funds in violation of the protocol's compliance model.

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

**File:** core/contracts/EndpointTx.sol (L374-376)
```text
        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
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
