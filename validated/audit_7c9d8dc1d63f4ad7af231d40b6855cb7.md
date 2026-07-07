### Title
Linked Signer Can Overwrite Its Own Authorization State via Fast-Mode `LinkSigner`, Enabling Persistent Subaccount Compromise — (File: `core/contracts/EndpointTx.sol`)

---

### Summary

The fast-mode `LinkSigner` transaction handler in `EndpointTx.sol` passes `allowLinkedSigner = true` to `validateSignedTx`, meaning the **currently registered linked signer** can sign a new `LinkSigner` transaction to replace itself with any attacker-controlled address. Because the `linkedSigners` authorization state is mutable by the very credential it protects, a compromised linked signer can permanently maintain control over a subaccount — draining funds and blocking the owner's revocation attempts — without ever needing the subaccount owner's private key.

---

### Finding Description

`EndpointTx.processTransactionImpl` handles the fast-mode `LinkSigner` transaction type at lines 576–590:

```solidity
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
        true          // <-- allowLinkedSigner
    );
    linkedSigners[signedTx.tx.sender] = address(
        uint160(bytes20(signedTx.tx.signer))
    );
``` [1](#0-0) 

`validateSignedTx` with `allowLinkedSigner = true` calls `validateSignature`, which in turn calls `verifier.validateSignature` passing `getLinkedSigner(sender)` as the accepted signer:

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

`verifier.validateSignature` accepts the signature if it recovers to **either** the subaccount owner address **or** the linked signer:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

The result: the linked signer can sign a `LinkSigner` transaction that **writes a new value into `linkedSigners[subaccount]`** — the very mapping that defines who is authorized to act on the subaccount. The authorization state protects itself with the credential it stores, creating a circular trust that a compromised credential can exploit.

The `linkedSigners` mapping is declared `internal` in `EndpointStorage`, but all EVM storage is publicly readable; the attacker can trivially read the current nonce for the subaccount owner's address from `nonces[address]` to construct a valid signed transaction. [4](#0-3) 

The slow-mode `LinkSigner` path (lines 232–239) does allow the subaccount owner to revoke by submitting a `msg.sender`-authenticated transaction, but it carries a hardcoded **3-day delay** (`SLOW_MODE_TX_DELAY`): [5](#0-4) [6](#0-5) 

During that 3-day window the attacker retains the linked signer role and can sign `WithdrawCollateral` (also `allowLinkedSigner = true`, lines 418–424) to drain all collateral before the revocation is processed. [7](#0-6) 

---

### Impact Explanation

A compromised linked signer key (e.g., a trading-bot key stored on a workstation) is sufficient to:

1. **Overwrite the `linkedSigners` entry** for the victim subaccount to an attacker-controlled address — without the subaccount owner's private key.
2. **Drain all collateral** via fast-mode `WithdrawCollateral` signed by the new linked signer, before the owner's 3-day slow-mode revocation is processed.
3. **Re-assert control** after each slow-mode revocation by immediately submitting another fast-mode `LinkSigner`, since the attacker controls the current linked signer at the time of each revocation attempt.

The corrupted state delta is `linkedSigners[victim_subaccount]` → attacker address, followed by a full balance drain through `clearinghouse.withdrawCollateral`.

---

### Likelihood Explanation

Linked signers are explicitly designed for delegation to automated systems (trading bots, API keys). These keys are stored on servers or workstations — exactly the "data at rest" threat model of the reference report. A key compromise (stolen server credentials, unlocked workstation, leaked `.env` file) is a realistic, non-privileged attacker path that requires no sequencer or governance access. The protocol's own design encourages widespread use of linked signers, increasing the attack surface.

---

### Recommendation

Change `allowLinkedSigner` to `false` for the fast-mode `LinkSigner` handler so that only the subaccount owner's key can mutate the `linkedSigners` authorization state:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // only the subaccount owner may change the linked signer
);
```

This mirrors the principle of "move sensitive data to the strongest secure store on each platform": the credential that controls delegation must itself be protected by the highest-privilege key (the subaccount owner), not by the delegated key it governs.

---

### Proof of Concept

1. Alice registers subaccount `alice_sub` and sets `linkedSigners[alice_sub] = bot_key` via a valid fast-mode `LinkSigner` signed by Alice's key.
2. Eve steals `bot_key` (e.g., from Alice's unlocked workstation or a leaked `.env` file).
3. Eve reads `nonces[alice_address]` from on-chain storage to obtain the current nonce `N`.
4. Eve constructs `SignedLinkSigner { tx: { sender: alice_sub, signer: eve_address, nonce: N }, signature: sign(bot_key, digest) }` and submits it to the sequencer as a fast-mode transaction.
5. The sequencer calls `processTransactionImpl`; `validateSignedTx` passes because `bot_key == getLinkedSigner(alice_sub)` and `allowLinkedSigner = true`. `linkedSigners[alice_sub]` is now `eve_address`.
6. Eve immediately signs a fast-mode `WithdrawCollateral` with `eve_key` to drain Alice's collateral.
7. Alice submits a slow-mode `LinkSigner` to revoke. After 3 days it sets `linkedSigners[alice_sub] = 0`. Eve re-submits step 4 using `eve_key` (currently the linked signer) before the slow-mode tx is processed, restoring control. Alice's funds are already gone.

### Citations

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

**File:** core/contracts/EndpointTx.sol (L374-384)
```text
        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
        // TODO: to save on costs we could potentially just emit something
        // for now, we can just create a separate loop in the engine that queries the remote
        // sequencer for slow mode transactions, and ignore the possibility of a reorgy attack
        slowModeConfig = _slowModeConfig;
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

**File:** core/contracts/Verifier.sol (L297-303)
```text
        address recovered = ECDSA.recover(digest, signature);
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
```

**File:** core/contracts/EndpointStorage.sol (L34-50)
```text
    mapping(address => uint64) internal nonces;

    uint64 public nSubmissions;

    IEndpoint.SlowModeConfig internal slowModeConfig;
    mapping(uint64 => IEndpoint.SlowModeTx) internal slowModeTxs;

    struct Times {
        uint128 perpTime;
        uint128 spotTime;
    }

    Times internal times;

    mapping(uint32 => int128) internal sequencerFee;

    mapping(bytes32 => address) internal linkedSigners;
```
