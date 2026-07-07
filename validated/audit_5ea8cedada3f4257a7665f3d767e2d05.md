### Title
Missing Caller Authorization in `NlpProfitShare` Slow-Mode Transaction — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The `NlpProfitShare` branch inside `processSlowModeTransactionImpl` verifies that the transaction's `recipient` field matches the NLP pool owner, but never verifies that the actual caller (`sender`) is the owner of that recipient subaccount. Any unprivileged user can submit a `NlpProfitShare` slow-mode transaction targeting any pool, forcing an unauthorized profit-share distribution and draining the pool subaccount's collateral.

---

### Finding Description

`processSlowModeTransactionImpl` handles several slow-mode transaction types. For every sensitive type — `DepositCollateral`, `WithdrawCollateral`, `LinkSigner`, `ClaimBuilderFee` — the function calls `validateSender(txn.sender, sender)` to confirm that the address that submitted the slow-mode transaction is the owner of the subaccount being acted upon: [1](#0-0) 

```solidity
function validateSender(bytes32 txSender, address sender) internal view {
    require(
        address(uint160(bytes20(txSender))) == sender ||
            sender == address(this),
        ERR_SLOW_MODE_WRONG_SENDER
    );
}
```

For `NlpProfitShare`, however, the only authorization check is that `txn.recipient` encodes the pool owner's address: [2](#0-1) 

```solidity
} else if (txType == IEndpoint.TransactionType.NlpProfitShare) {
    ...
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
        txn.amount          // attacker-controlled
    );
```

There is **no** `validateSender(txn.recipient, sender)` call. The `sender` argument — the address that actually submitted the slow-mode transaction — is never compared against the pool owner. An attacker who knows the pool owner's subaccount bytes (public on-chain) can craft a `NlpProfitShare` transaction with:

- `txn.poolId` → any live NLP pool
- `txn.recipient` → the pool owner's subaccount (passes the existing check)
- `txn.amount` → any value up to the pool's full balance

The sequencer will process it, calling `clearinghouse.nlpProfitShare` and transferring `txn.amount` out of the pool subaccount into the pool owner's personal subaccount — without the pool owner ever requesting it.

---

### Impact Explanation

`clearinghouse.nlpProfitShare` moves collateral from the NLP pool subaccount to the recipient subaccount. An attacker can:

1. **Drain the pool subaccount** by setting `txn.amount` to the pool's full balance, collapsing the pool's collateral backing.
2. **Harm NLP depositors**: NLP token holders' redemption value is backed by the pool subaccount. Draining it makes their tokens undercollateralized or worthless.
3. **Trigger at will**: The attacker can repeat this at any time, preventing the pool from accumulating value.

The funds land in the pool owner's subaccount (not the attacker's), but the pool itself is gutted, constituting a direct asset loss for NLP depositors.

---

### Likelihood Explanation

- The attack requires no special privileges — any address can submit a slow-mode transaction.
- All required inputs (`poolId`, pool owner subaccount bytes) are publicly readable on-chain.
- The sequencer processes slow-mode transactions without additional authorization beyond what the contract enforces.
- No front-running or timing dependency is required.

Likelihood: **High**.

---

### Recommendation

Add a `validateSender` call for the `NlpProfitShare` branch, mirroring every other user-initiated slow-mode transaction type:

```solidity
} else if (txType == IEndpoint.TransactionType.NlpProfitShare) {
    IEndpoint.NlpProfitShare memory txn = abi.decode(
        transaction[1:],
        (IEndpoint.NlpProfitShare)
    );
    require(txn.poolId > 0 && txn.poolId < nlpPools.length, ERR_INVALID_NLP_POOL);
    require(nlpPools[txn.poolId].owner != address(0), ERR_INVALID_NLP_POOL);
    require(
        address(uint160(bytes20(txn.recipient))) == nlpPools[txn.poolId].owner,
        ERR_UNAUTHORIZED
    );
+   validateSender(txn.recipient, sender);   // <-- missing check
    requireSubaccount(txn.recipient);
    require(!RiskHelper.isIsolatedSubaccount(txn.recipient));
    clearinghouse.nlpProfitShare(
        nlpPools[txn.poolId].subaccount,
        txn.recipient,
        txn.amount
    );
```

This ensures only the pool owner's address can submit a `NlpProfitShare` slow-mode transaction, exactly as the Frakt patch enforces `stake_info.stake_owner == ctx.accounts.user.key()`.

---

### Proof of Concept

1. Pool `poolId = 1` exists with `nlpPools[1].owner = 0xPoolOwner` and pool subaccount `nlpPools[1].subaccount = POOL_SUB`.
2. Attacker constructs `NlpProfitShare { poolId: 1, recipient: bytes32(uint256(uint160(0xPoolOwner))), amount: POOL_BALANCE }`.
3. Attacker calls `submitSlowModeTransaction(transaction)` from their own address `0xAttacker`.
4. Sequencer calls `processSlowModeTransactionImpl(0xAttacker, transaction)`.
5. The `require(address(uint160(bytes20(txn.recipient))) == nlpPools[1].owner)` check passes — `txn.recipient` correctly encodes `0xPoolOwner`.
6. `validateSender` is never called — `0xAttacker != 0xPoolOwner` is never checked.
7. `clearinghouse.nlpProfitShare(POOL_SUB, txn.recipient, POOL_BALANCE)` executes, transferring the entire pool balance out of `POOL_SUB`.
8. NLP depositors' collateral backing is zeroed. [2](#0-1) [1](#0-0) [3](#0-2)

### Citations

**File:** core/contracts/EndpointTx.sol (L17-23)
```text
    function validateSender(bytes32 txSender, address sender) internal view {
        require(
            address(uint160(bytes20(txSender))) == sender ||
                sender == address(this),
            ERR_SLOW_MODE_WRONG_SENDER
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L209-239)
```text
        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            IEndpoint.DepositCollateral memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositCollateral)
            );
            validateSender(txn.sender, sender);
            _recordSubaccount(txn.sender);
            clearinghouse.depositCollateral(txn);
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
        } else if (txType == IEndpoint.TransactionType.DepositInsurance) {
            clearinghouse.depositInsurance(transaction);
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.LinkSigner memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.LinkSigner)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
```

**File:** core/contracts/EndpointTx.sol (L289-313)
```text
        } else if (txType == IEndpoint.TransactionType.NlpProfitShare) {
            IEndpoint.NlpProfitShare memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.NlpProfitShare)
            );
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
