### Title
Missing `chainId` in Schnorr Batch Digest Enables Cross-Chain Replay of Sequencer Submissions — (`File: core/contracts/Endpoint.sol`)

---

### Summary

`submitTransactionsChecked` in `Endpoint.sol` computes the Schnorr verification digest over `idx` and raw transaction bytes, but **omits `block.chainid`**. A Schnorr signature produced by the sequencer for a batch on chain A is therefore cryptographically valid on any other chain where the same sequencer address is deployed and the `nSubmissions` counter matches `idx`. This is a direct structural analog to the Linea missing-`chainId` public-input flaw: the verifier cannot distinguish a legitimately signed batch for the current chain from one signed for a different chain.

---

### Finding Description

In `Endpoint.submitTransactionsChecked`, the digest passed to `verifier.requireValidSignature` is assembled as:

```solidity
bytes32 digest = keccak256(abi.encode(idx));
for (uint256 i = 0; i < transactions.length; ++i) {
    digest = keccak256(abi.encodePacked(digest, transactions[i]));
}
verifier.requireValidSignature(digest, e, s, signerBitmask);
``` [1](#0-0) 

`block.chainid` is entirely absent from this digest. The Schnorr signature (`e`, `s`, `signerBitmask`) is therefore chain-agnostic: any chain where the sequencer address is the same and `nSubmissions == idx` will accept the same `(e, s, signerBitmask)` tuple.

This stands in direct contrast to `Verifier.requireValidTxSignatures`, the ECDSA-based individual-transaction path, which **explicitly binds the digest to the chain**:

```solidity
bytes32 data = keccak256(
    abi.encodePacked(uint256(block.chainid), uint256(idx), txn)
);
``` [2](#0-1) 

The two verification paths are therefore inconsistent: ECDSA-signed individual transactions are chain-bound; Schnorr-signed batches are not.

The `requireValidSignature` function itself performs no chain-binding — it only verifies the Schnorr equation over the raw `message` bytes it receives: [3](#0-2) 

The `validateSubmissionIdx` guard only enforces monotonic ordering within a single deployment; it provides no cross-chain protection: [4](#0-3) 

---

### Impact Explanation

Every transaction type processed through `processTransactionImpl` — including `WithdrawCollateral`, `WithdrawCollateralV2`, `MintNlp`, `BurnNlp`, `TransferQuote`, `LiquidateSubaccount`, and `LinkSigner` — is executed when the sequencer submits a batch via `submitTransactionsChecked`. [5](#0-4) 

If a signed batch from chain A is replayed on chain B:

- **Collateral theft**: A `WithdrawCollateral` or `WithdrawCollateralV2` transaction processed on chain A is replayed on chain B, draining the victim subaccount's collateral on chain B without any corresponding deposit on chain B.
- **Accounting corruption**: Trades, liquidations, NLP mints/burns, and quote transfers from chain A execute on chain B, corrupting its position and balance state.
- **Signer hijack**: A `LinkSigner` transaction replayed on chain B installs an attacker-controlled linked signer on a subaccount that never authorized it on chain B. [6](#0-5) 

---

### Likelihood Explanation

Nado is a high-performance DEX explicitly designed for EVM-compatible chains. Multi-chain deployment (e.g., mainnet + testnet, or two L2s) is a realistic operational scenario. The sequencer is a single privileged address; the same operator key is almost certainly reused across deployments. Both chains start with `nSubmissions = 0`, so the first batch (`idx = 0`) is immediately replayable, and every subsequent batch remains replayable as long as both chains advance in lockstep. A chain fork — which produces an identical `nSubmissions` state on both branches — makes replay trivially possible without any malicious intent from the sequencer. The sequencer does not need to be compromised: an honest sequencer operating on two chains with the same key produces valid signatures for both.

---

### Recommendation

Bind the Schnorr digest to the current chain by including `block.chainid` in the initial hash in `submitTransactionsChecked`:

```solidity
// Before (vulnerable):
bytes32 digest = keccak256(abi.encode(idx));

// After (fixed):
bytes32 digest = keccak256(abi.encode(block.chainid, idx));
``` [7](#0-6) 

This mirrors the chain-binding already present in `requireValidTxSignatures` and aligns the Schnorr batch path with the ECDSA individual-transaction path.

---

### Proof of Concept

1. Nado is deployed on chain A (`chainId = 57073`, Ink mainnet) and chain B (`chainId = 763373`, Ink testnet) with the **same sequencer address** and both starting at `nSubmissions = 0`.
2. User X deposits collateral on chain A and submits a `WithdrawCollateral` transaction to the sequencer.
3. The sequencer signs batch `idx = 0` containing the `WithdrawCollateral` for user X and submits it on chain A via `submitTransactionsChecked(0, [withdrawTx], e, s, bitmask)`. The call succeeds; user X's collateral is withdrawn on chain A.
4. The sequencer (same address, same keys) now calls `submitTransactionsChecked(0, [withdrawTx], e, s, bitmask)` on chain B. `validateSubmissionIdx` passes because chain B also has `nSubmissions = 0`. `requireValidSignature` passes because the digest — computed identically without `chainId` — is the same bytes32 value on both chains.
5. User X's `WithdrawCollateral` executes on chain B, draining chain B's collateral pool for a deposit that was never made on chain B.

The corrupted state delta is: `spotEngine.balance[user X][productId]` on chain B is decremented by `amount` with no corresponding deposit, and `clearinghouse.withdrawCollateral` transfers real tokens out of chain B's custody. [8](#0-7)

### Citations

**File:** core/contracts/Endpoint.sol (L86-88)
```text
    function validateSubmissionIdx(uint64 idx) private view {
        require(idx == nSubmissions, ERR_INVALID_SUBMISSION_INDEX);
    }
```

**File:** core/contracts/Endpoint.sol (L283-287)
```text
        bytes32 digest = keccak256(abi.encode(idx));
        for (uint256 i = 0; i < transactions.length; ++i) {
            digest = keccak256(abi.encodePacked(digest, transactions[i]));
        }
        verifier.requireValidSignature(digest, e, s, signerBitmask);
```

**File:** core/contracts/Verifier.sol (L140-158)
```text
    function requireValidSignature(
        bytes32 message,
        bytes32 e,
        bytes32 s,
        uint8 signerBitmask
    ) public {
        require(checkQuorum(signerBitmask));
        Point memory pubkey = getAggregatePubkey(signerBitmask);
        require(
            verify(
                pubkey.y % 2 == 0 ? 27 : 28,
                bytes32(pubkey.x),
                message,
                e,
                s
            ),
            "Verification failed"
        );
    }
```

**File:** core/contracts/Verifier.sol (L267-269)
```text
        bytes32 data = keccak256(
            abi.encodePacked(uint256(block.chainid), uint256(idx), txn)
        );
```

**File:** core/contracts/EndpointTx.sol (L387-436)
```text
    function processTransactionImpl(bytes calldata transaction) public {
        IEndpoint.TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );
        if (txType == IEndpoint.TransactionType.LiquidateSubaccount) {
            IEndpoint.SignedLiquidateSubaccount memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedLiquidateSubaccount)
            );
            if (signedTx.tx.sender != N_ACCOUNT) {
                validateSignedTx(
                    signedTx.tx.sender,
                    signedTx.tx.nonce,
                    transaction,
                    signedTx.signature,
                    true
                );
                // No liquidation fee for finalization (productId == uint32.max) because:
                // 1) The liquidator receives no profit from finalization
                // 2) Finalization can only occur once per underwater subaccount, eliminating
                //    sybil attack concerns that would otherwise require a fee deterrent.
                if (signedTx.tx.productId != type(uint32).max) {
                    chargeFee(signedTx.tx.sender, LIQUIDATION_FEE);
                }
            }
            clearinghouse.liquidateSubaccount(signedTx.tx);
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
