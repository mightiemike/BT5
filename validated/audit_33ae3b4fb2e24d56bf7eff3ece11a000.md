### Title
Linked Signer Cannot Submit Slow-Mode `WithdrawCollateral` on Behalf of Subaccount Owner — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

The `validateSender` function used in the slow-mode transaction path does not recognize a subaccount's linked signer as an authorized actor. This means a linked signer — which is explicitly designed to act on behalf of a subaccount owner — is blocked from submitting a slow-mode `WithdrawCollateral` (and `ClaimBuilderFee`) transaction, even though the same linked signer is fully authorized to perform these actions through the fast (sequencer-submitted) path.

---

### Finding Description

Nado supports a delegation mechanism via `linkedSigners`: a subaccount owner can register a linked signer address that is authorized to sign transactions on their behalf. This is set via `LinkSigner` and is honored in all fast-path transaction types through `validateSignedTx(..., allowLinkedSigner: true)`.

The slow-mode path — the protocol's safety fallback for sequencer downtime — uses a different authorization check: `validateSender`. [1](#0-0) 

```solidity
function validateSender(bytes32 txSender, address sender) internal view {
    require(
        address(uint160(bytes20(txSender))) == sender ||
            sender == address(this),
        ERR_SLOW_MODE_WRONG_SENDER
    );
}
```

This check only passes if `sender` is the address embedded in the subaccount `bytes32` (i.e., the original owner) or `address(this)`. It does **not** consult `linkedSigners[txSender]`.

In `processSlowModeTransactionImpl`, both `WithdrawCollateral` and `ClaimBuilderFee` are gated by this check: [2](#0-1) [3](#0-2) 

By contrast, the fast-path `WithdrawCollateral` handler calls `validateSignedTx` with `allowLinkedSigner: true`, explicitly permitting the linked signer: [4](#0-3) 

The `sender` stored in a `SlowModeTx` is `msg.sender` at submission time: [5](#0-4) 

So when a linked signer calls `submitSlowModeTransaction` with a `WithdrawCollateral` payload for subaccount `0xOwner...`, the recorded `sender` is the linked signer's address. When the transaction is later executed, `validateSender(txn.sender, linkedSignerAddress)` compares the linked signer address against the owner address embedded in `txn.sender` — they differ, and the call reverts.

---

### Impact Explanation

A linked signer is blocked from withdrawing collateral via the slow-mode path on behalf of the subaccount owner. This is most severe during sequencer downtime, which is precisely the scenario slow-mode is designed for. If the subaccount owner has delegated signing authority to a linked signer (e.g., a hot wallet, a smart contract, or an automated keeper) and the original owner key is unavailable, the linked signer cannot rescue funds through the slow-mode safety valve. The funds remain locked in the protocol until the sequencer resumes or the original owner key is used directly.

The same blockage applies to `ClaimBuilderFee` via slow-mode.

---

### Likelihood Explanation

Medium. The scenario requires: (1) a subaccount with a registered linked signer, and (2) the linked signer attempting to use the slow-mode path (either during sequencer downtime or by design). Both conditions are realistic and supported by the protocol's documented design. The linked signer feature is a first-class protocol primitive, and slow-mode is the documented fallback for sequencer unavailability.

---

### Recommendation

Extend `validateSender` to also accept the registered linked signer of the subaccount:

```solidity
function validateSender(bytes32 txSender, address sender) internal view {
    require(
        address(uint160(bytes20(txSender))) == sender ||
            sender == address(this) ||
            linkedSigners[txSender] == sender,
        ERR_SLOW_MODE_WRONG_SENDER
    );
}
```

This mirrors the `allowLinkedSigner` logic already present in `validateSignature` / `validateCompactSignature` and makes the slow-mode path consistent with the fast-path authorization model.

---

### Proof of Concept

1. Alice owns subaccount `0xAlice000000000000000000000000000000000000` + `bytes12(name)`.
2. Alice registers `0xLinkedSigner` via a `LinkSigner` transaction. `linkedSigners[aliceSubaccount] = 0xLinkedSigner`.
3. The sequencer goes offline. Alice's original key is unavailable; only `0xLinkedSigner` is accessible.
4. `0xLinkedSigner` calls `submitSlowModeTransaction` with a `WithdrawCollateral` payload specifying `txn.sender = aliceSubaccount`.
   - `submitSlowModeTransactionImpl` records `sender = 0xLinkedSigner` in `slowModeTxs`.
5. After the timeout, anyone calls `executeSlowModeTransaction`.
6. `processSlowModeTransactionImpl` is invoked with `sender = 0xLinkedSigner`.
7. `validateSender(aliceSubaccount, 0xLinkedSigner)` checks: `address(uint160(bytes20(aliceSubaccount))) == 0xLinkedSigner` → `0xAlice... != 0xLinkedSigner` → **reverts with `ERR_SLOW_MODE_WRONG_SENDER`**.
8. Alice's collateral cannot be withdrawn via slow-mode by the linked signer. Funds are inaccessible until the sequencer resumes or the original owner key is used. [1](#0-0) [2](#0-1) [6](#0-5)

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

**File:** core/contracts/EndpointTx.sol (L316-327)
```text
        } else if (txType == IEndpoint.TransactionType.ClaimBuilderFee) {
            IEndpoint.ClaimBuilderFee memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.ClaimBuilderFee)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            IOffchainExchange(offchainExchange).claimBuilderFee(
                txn.sender,
                txn.builderId
            );
        } else {
```

**File:** core/contracts/EndpointTx.sol (L341-380)
```text
        address sender = msg.sender;

        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            revert();
        } else if (txType == IEndpoint.TransactionType.DepositInsurance) {
            IEndpoint.DepositInsurance memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositInsurance)
            );
            require(
                txn.amount >= uint128(SLOW_MODE_FEE),
                ERR_DEPOSIT_TOO_SMALL
            );
            handleDepositTransfer(_getQuote(), sender, uint256(txn.amount));
        } else if (
            txType == IEndpoint.TransactionType.WithdrawInsurance ||
            txType == IEndpoint.TransactionType.DelistProduct ||
            txType == IEndpoint.TransactionType.DumpFees ||
            txType == IEndpoint.TransactionType.RebalanceXWithdraw ||
            txType == IEndpoint.TransactionType.UpdateTierFeeRates ||
            txType == IEndpoint.TransactionType.AddNlpPool ||
            txType == IEndpoint.TransactionType.UpdateNlpPool ||
            txType == IEndpoint.TransactionType.DeleteNlpPool ||
            txType == IEndpoint.TransactionType.ForceRebalanceNlpPool ||
            txType == IEndpoint.TransactionType.NlpProfitShare ||
            txType == IEndpoint.TransactionType.UpdateBuilder
        ) {
            require(sender == owner());
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }

        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
```

**File:** core/contracts/EndpointTx.sol (L418-424)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
```

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```
