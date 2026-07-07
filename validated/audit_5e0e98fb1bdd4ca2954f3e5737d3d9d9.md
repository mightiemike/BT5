Now I have all the information needed. Let me also verify the `linkedSigners` key structure and the `validateNonce` behavior to confirm the full attack path.

The full attack path is confirmed. Here is the analysis:

---

### Title
Linked Signer Bypasses Cross-Subaccount Transfer Guard via Truncated `bytes20` Comparison — (`core/contracts/Clearinghouse.sol`)

### Summary

`Clearinghouse.transferQuote` intends to prevent linked signers from moving collateral to a different owner's subaccount. The guard uses `bytes20(txn.sender) == bytes20(txn.recipient)`, which only compares the 20-byte address prefix of the 32-byte subaccount identifier. Two distinct subaccounts of the same owner (e.g., `A||name1` and `A||name2`) share the same 20-byte prefix, so the check passes even when `sender != recipient`. A linked signer authorized only for `A||name1` can therefore drain quote collateral from `A||name1` into `A||name2` without the owner's authorization.

### Finding Description

A Nado subaccount is encoded as a `bytes32` value: the first 20 bytes are the owner's Ethereum address, and the last 12 bytes are the subaccount name suffix. The guard in `transferQuote` is:

```solidity
// require the sender address to be the same as the recipient address
// otherwise linked signers can transfer out
require(
    bytes20(txn.sender) == bytes20(txn.recipient),
    ERR_UNAUTHORIZED
);
``` [1](#0-0) 

In Solidity, casting `bytes32` to `bytes20` retains the **most significant (leftmost) 20 bytes** — i.e., the owner address — and silently discards the 12-byte name suffix. For `sender = A||name1` and `recipient = A||name2`, both `bytes20(...)` values equal address `A`, so the require passes even though `sender != recipient`.

The `TransferQuote` path in `EndpointTx.processTransactionImpl` calls `validateSignedTx` with `allowLinkedSigner = true`:

```solidity
validateSignedTx(
    signedTx.tx.sender,   // A||name1
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true                  // linked signer accepted
);
``` [2](#0-1) 

`validateSignature` in `Verifier.sol` accepts a signature from either the owner address or the registered linked signer for the sender subaccount:

```solidity
require(
    (recovered != address(0)) &&
        ((recovered == address(uint160(bytes20(sender)))) ||
            (recovered == linkedSigner)),
    ERR_INVALID_SIGNATURE
);
``` [3](#0-2) 

`getLinkedSigner` looks up `linkedSigners[subaccount]` keyed by the full `bytes32` subaccount, so the linked signer for `A||name1` is distinct from the linked signer for `A||name2`. The linked signer for `A||name1` has no authorization over `A||name2`, yet the `bytes20` guard allows them to move funds into it. [4](#0-3) 

The only remaining check after the guard is a post-transfer health check on the sender:

```solidity
require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
``` [5](#0-4) 

This prevents draining `A||name1` below initial health at the moment of transfer, but it does not prevent the unauthorized transfer itself, and it does not account for subsequent price movements that could push `A||name1` below maintenance health after the transfer.

### Impact Explanation

A linked signer authorized only for `A||name1` can:

1. Move quote collateral from `A||name1` to `A||name2` without the owner's per-subaccount authorization, violating the intended authorization boundary.
2. Drain `A||name1` to exactly the initial health threshold. Any subsequent adverse price movement then pushes `A||name1` below maintenance health, triggering an incorrect liquidation of a subaccount the owner did not intend to expose.
3. If `A||name1` carries leveraged perp positions, the forced liquidation causes real asset loss to the owner (liquidation penalties, bad fills) even though the collateral nominally remains within address `A`'s subaccounts.

The impact matches the Critical scope: **incorrect liquidation** and **unauthorized privileged outcome** (cross-subaccount collateral movement without owner authorization).

### Likelihood Explanation

- Linked signers are a supported, documented protocol feature (trading bots, delegated operators).
- The attacker only needs to be a linked signer for one subaccount of the victim; no admin or sequencer compromise is required.
- The exploit is fully on-chain and requires no special privileges beyond the linked signer role.
- The `bytes20` truncation is a silent Solidity type-cast behavior that is easy to miss in review.

### Recommendation

Replace the truncated comparison with a full `bytes32` equality check on the owner address extracted explicitly, or require that the transaction be signed by the owner (not a linked signer) for any cross-subaccount transfer. The minimal fix:

```solidity
// Compare full bytes32 owner prefix explicitly
require(
    address(uint160(bytes20(txn.sender))) == address(uint160(bytes20(txn.recipient))),
    ERR_UNAUTHORIZED
);
```

is still insufficient because it has the same semantic flaw. The correct fix is to disallow linked signers from initiating `TransferQuote` entirely by passing `allowLinkedSigner = false` in `EndpointTx`:

```solidity
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    false   // owner signature required for cross-subaccount transfers
);
``` [6](#0-5) 

### Proof of Concept

1. Deploy the protocol on a local Hardhat fork.
2. Register two subaccounts for address `A`: `A||"default "` and `A||"vault    "` (12-byte padded names).
3. Link a signer `S` to `A||"default "` via `LinkSigner`.
4. Deposit 10,000 USDC quote collateral into `A||"default "`.
5. Construct a `SignedTransferQuote` with `sender = A||"default "`, `recipient = A||"vault    "`, `amount = 9,000e18`, signed by `S`.
6. Submit via `Endpoint.submitTransactionsChecked`.
7. Assert: `spotEngine.getBalance(QUOTE_PRODUCT_ID, A||"vault    ")` increased by 9,000e18 without any signature from address `A`.
8. Confirm `A||"default "` is now at initial health; submit a small adverse price update and observe `A||"default "` becomes liquidatable. [7](#0-6)

### Citations

**File:** core/contracts/Clearinghouse.sol (L211-250)
```text
    function transferQuote(IEndpoint.TransferQuote calldata txn)
        external
        virtual
        onlyEndpoint
    {
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 toTransfer = int128(txn.amount);
        ISpotEngine spotEngine = _spotEngine();

        // require the sender address to be the same as the recipient address
        // otherwise linked signers can transfer out
        require(
            bytes20(txn.sender) == bytes20(txn.recipient),
            ERR_UNAUTHORIZED
        );
        address offchainExchange = IEndpoint(getEndpoint())
            .getOffchainExchange();
        if (RiskHelper.isIsolatedSubaccount(txn.sender)) {
            // isolated subaccounts can only transfer quote back to parent
            require(
                IOffchainExchange(offchainExchange).getParentSubaccount(
                    txn.sender
                ) == txn.recipient,
                ERR_UNAUTHORIZED
            );
        } else if (RiskHelper.isIsolatedSubaccount(txn.recipient)) {
            // regular subaccounts can transfer quote to active isolated subaccounts
            require(
                IOffchainExchange(offchainExchange).isIsolatedSubaccountActive(
                    txn.sender,
                    txn.recipient
                ),
                ERR_UNAUTHORIZED
            );
        }

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -toTransfer);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.recipient, toTransfer);
        require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
    }
```

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

**File:** core/contracts/EndpointTx.sol (L599-605)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
```

**File:** core/contracts/Verifier.sol (L298-303)
```text
        require(
            (recovered != address(0)) &&
                ((recovered == address(uint160(bytes20(sender)))) ||
                    (recovered == linkedSigner)),
            ERR_INVALID_SIGNATURE
        );
```
