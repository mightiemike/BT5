### Title
Linked Signer Can Unilaterally Rotate Itself via `LinkSigner` Transaction, Enabling Full Subaccount Collateral Drain — (`core/contracts/EndpointTx.sol`)

---

### Summary

In `EndpointTx.processTransactionImpl`, the `LinkSigner` transaction type is validated with `allowLinkedSigner = true`. This means the **currently installed linked signer** — not just the subaccount owner — can sign a `LinkSigner` transaction to replace itself with any arbitrary address. Because `WithdrawCollateral` also accepts linked-signer signatures, a compromised or malicious linked signer can atomically rotate authority to an attacker-controlled address and drain the subaccount's entire collateral balance in the same sequencer batch.

---

### Finding Description

**Root cause — `EndpointTx.sol:576–590`:**

```solidity
} else if (txType == IEndpoint.TransactionType.LinkSigner) {
    IEndpoint.SignedLinkSigner memory signedTx = abi.decode(
        transaction[1:], (IEndpoint.SignedLinkSigner)
    );
    validateSignedTx(
        signedTx.tx.sender,
        signedTx.tx.nonce,
        transaction,
        signedTx.signature,
        true                          // @audit allowLinkedSigner = true
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` routes through `validateSignature`, which passes the current linked signer to the verifier as an accepted co-signer:

```solidity
function validateSignature(..., bool allowLinkedSigner) internal virtual {
    verifier.validateSignature(
        sender,
        allowLinkedSigner ? getLinkedSigner(sender) : address(0),
        digest,
        signature
    );
}
``` [2](#0-1) 

The `linkedSigners` mapping is the sole source of truth for who is authorized to act on behalf of a subaccount: [3](#0-2) 

**Contrast with the slow-mode path** (`processSlowModeTransactionImpl:232–239`), which uses `validateSender(txn.sender, sender)` — a check that enforces `address(uint160(bytes20(txSender))) == msg.sender`, i.e., only the actual subaccount owner address can submit a slow-mode `LinkSigner`. The fast (sequencer) path has no equivalent restriction. [4](#0-3) 

**`WithdrawCollateral` also accepts linked-signer signatures** (`allowLinkedSigner = true`), completing the drain path: [5](#0-4) 

**Attack sequence:**

1. Victim sets linked signer to address `B` (e.g., a trading bot or exchange hot wallet).
2. `B` is compromised or acts maliciously. `B` signs a `LinkSigner` transaction: `{sender: victimSubaccount, signer: attackerAddress}`.
3. `B` (or the attacker) submits this transaction to the sequencer. The sequencer validates the signature — it is valid because `B == getLinkedSigner(victimSubaccount)` — and processes it: `linkedSigners[victimSubaccount] = attackerAddress`.
4. In the same sequencer batch (or immediately after), the attacker signs `WithdrawCollateral{sender: victimSubaccount, amount: fullBalance}`.
5. The sequencer validates the signature — valid because `attackerAddress == getLinkedSigner(victimSubaccount)` — and processes the withdrawal.
6. The victim's entire collateral balance is transferred out.

The victim has no on-chain signal that the linked signer was rotated before the withdrawal executes. The two transactions can be submitted atomically in a single `submitTransactionsChecked` call. [6](#0-5) 

---

### Impact Explanation

A compromised or malicious linked signer can drain **100% of a subaccount's collateral** across all deposited products. The `linkedSigners` mapping is never cleared or validated against the subaccount owner's address at dispatch time; the only check is that the signature matches either the owner or the current linked signer. Once the linked signer rotates authority to an attacker address, the attacker has full withdrawal rights indistinguishable from the legitimate owner. [7](#0-6) 

---

### Likelihood Explanation

Linked signers are the standard mechanism for delegating signing authority to trading bots, exchange hot wallets, and automated market-making systems. Any of these can be compromised via key theft, supply-chain attack, or insider threat. The attack requires no admin access, no sequencer compromise, and no governance action — only a valid signature from the currently registered linked signer address. The trigger is a normal sequencer-processed transaction batch, reachable by any party that can submit transactions to the sequencer API.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the `LinkSigner` branch in `processTransactionImpl`. Only the subaccount owner (the address encoded in the first 20 bytes of the `bytes32` subaccount key) should be permitted to change the linked signer. The slow-mode path already enforces this correctly via `validateSender`; the fast path must match it.

```solidity
// EndpointTx.sol — processTransactionImpl, LinkSigner branch
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only subaccount owner may rotate the linked signer
);
``` [1](#0-0) 

---

### Proof of Concept

```
1. Alice deposits 10,000 USDC into subaccount alice_sub.
2. Alice calls submitSlowModeTransaction(LinkSigner{sender: alice_sub, signer: botAddress}).
   → linkedSigners[alice_sub] = botAddress
3. botAddress is compromised. Attacker constructs:
     Tx1 = LinkSigner{sender: alice_sub, signer: attackerEOA}, signed by botAddress
     Tx2 = WithdrawCollateral{sender: alice_sub, productId: USDC, amount: 10000e6}, signed by attackerEOA
4. Attacker submits [Tx1, Tx2] to the sequencer in a single batch.
5. Sequencer calls submitTransactionsChecked(idx, [Tx1, Tx2], ...).
   - Tx1: validateSignedTx(alice_sub, ..., true) → sig from botAddress == getLinkedSigner(alice_sub) ✓
         linkedSigners[alice_sub] = attackerEOA
   - Tx2: validateSignedTx(alice_sub, ..., true) → sig from attackerEOA == getLinkedSigner(alice_sub) ✓
         clearinghouse.withdrawCollateral(alice_sub, USDC, 10000e6, ...)
6. Alice's 10,000 USDC is transferred to attackerEOA. alice_sub balance = 0.
```

### Citations

**File:** core/contracts/EndpointTx.sol (L143-157)
```text
    function getLinkedSigner(bytes32 subaccount)
        public
        view
        virtual
        returns (address)
    {
        return
            RiskHelper.isIsolatedSubaccount(subaccount)
                ? linkedSigners[
                    IOffchainExchange(offchainExchange).getParentSubaccount(
                        subaccount
                    )
                ]
                : linkedSigners[subaccount];
    }
```

**File:** core/contracts/EndpointTx.sol (L172-184)
```text
    function validateSignature(
        bytes32 sender,
        bytes32 digest,
        bytes memory signature,
        bool allowLinkedSigner
    ) internal virtual {
        verifier.validateSignature(
            sender,
            allowLinkedSigner ? getLinkedSigner(sender) : address(0),
            digest,
            signature
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L232-239)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.LinkSigner memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.LinkSigner)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
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

**File:** core/contracts/EndpointStorage.sol (L50-50)
```text
    mapping(bytes32 => address) internal linkedSigners;
```

**File:** core/contracts/Endpoint.sol (L271-294)
```text
    function submitTransactionsChecked(
        uint64 idx,
        bytes[] calldata transactions,
        bytes32 e,
        bytes32 s,
        uint8 signerBitmask
    ) external {
        validateSubmissionIdx(idx);
        require(msg.sender == sequencer);
        // TODO: if one of these transactions fails this means the sequencer is in an error state
        // we should probably record this, and engage some sort of recovery mode

        bytes32 digest = keccak256(abi.encode(idx));
        for (uint256 i = 0; i < transactions.length; ++i) {
            digest = keccak256(abi.encodePacked(digest, transactions[i]));
        }
        verifier.requireValidSignature(digest, e, s, signerBitmask);

        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            nSubmissions += 1;
        }
    }
```
