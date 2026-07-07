### Title
Use of Deprecated `draft-EIP712Upgradeable` with Cached Domain Separator Enables Cross-Chain Signature Replay — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

`EndpointTx.sol` and `Endpoint.sol` import and use `draft-EIP712Upgradeable.sol`, a deprecated draft implementation of EIP-712. The draft version caches the domain separator (including `chainId`) at initialization time rather than computing it dynamically per call. This is the direct Nado analog to M08: using a draft/experimental library feature that has since been superseded by a stable, corrected version. The concrete impact is that all user-signed transaction digests are hashed against a potentially stale domain separator, enabling cross-chain signature replay for withdrawals, liquidations, signer links, and quote transfers.

---

### Finding Description

Both `Endpoint.sol` and `EndpointTx.sol` import the draft OpenZeppelin EIP-712 implementation: [1](#0-0) [2](#0-1) 

`EndpointTx` inherits `EIP712Upgradeable` and calls `_hashTypedDataV4` directly in both overloads of `validateSignedTx`: [3](#0-2) [4](#0-3) 

`_hashTypedDataV4` internally calls `_domainSeparatorV4()`. In the **draft** version of `EIP712Upgradeable`, the domain separator is computed once during `__EIP712_init` and cached in storage. It is **not** recomputed on each call using the live `block.chainid`. The stable (non-draft) version was introduced precisely to fix this: it reads `block.chainid` dynamically and only falls back to the cached value when the chain ID has not changed.

`Endpoint.__EIP712_init` is called at deployment with `"Nado"` and `"0.0.1"`: [5](#0-4) 

After that point, the domain separator is frozen. Any change to `block.chainid` (chain fork, reorg, or cross-chain deployment with the same initial chain ID) will not be reflected.

Additionally, `Verifier.sol` also imports `draft-EIP712Upgradeable` but never uses `_hashTypedDataV4` or `_domainSeparatorV4` anywhere in its body — an unused draft import directly mirroring the M08 pattern: [6](#0-5) 

---

### Impact Explanation

Every user-signed transaction type that flows through `validateSignedTx` is affected:

- `WithdrawCollateral` / `WithdrawCollateralV2` — collateral theft via replayed withdrawal signature
- `LiquidateSubaccount` — unauthorized liquidation replay
- `LinkSigner` — attacker replays a `LinkSigner` transaction on a forked chain to hijack signer authority
- `TransferQuote` — replay of a quote transfer drains the sender's balance
- `MintNlp` / `BurnNlp` — replay of NLP mint/burn operations [7](#0-6) [8](#0-7) 

On a forked chain where `block.chainid` differs from the initialization-time chain ID, the domain separator used to verify signatures remains the pre-fork value. A signature produced on the original chain is therefore valid on the fork, and vice versa. An unprivileged attacker who observed any signed transaction on one chain can replay it on the other without any additional capability.

---

### Likelihood Explanation

The Nado protocol is deployed on EVM-compatible chains. EVM chains have forked before (ETH/ETC, BSC incidents). The protocol is also likely deployed across multiple chains (Arbitrum, Base, etc.). If any two deployments share the same initialization-time chain ID — or if a chain forks — the stale domain separator directly enables replay. The attacker-controlled entry path is `Endpoint.submitTransactionsChecked` → `processTransaction` → `EndpointTx.processTransactionImpl` → `validateSignedTx`, all reachable by the sequencer submitting a replayed transaction. No privileged access beyond observing on-chain signed transactions is required.

---

### Recommendation

Replace the draft import with the stable, finalized OpenZeppelin implementation in both `Endpoint.sol` and `EndpointTx.sol`:

```solidity
// Remove:
import "@openzeppelin/contracts-upgradeable/utils/cryptography/draft-EIP712Upgradeable.sol";

// Replace with:
import "@openzeppelin/contracts-upgradeable/utils/cryptography/EIP712Upgradeable.sol";
```

The stable `EIP712Upgradeable` computes the domain separator dynamically using `block.chainid`, eliminating the stale-separator risk. Also remove the unused `draft-EIP712Upgradeable` import from `Verifier.sol`.

---

### Proof of Concept

1. Nado `Endpoint` is initialized on Chain A (chainId = 42161). The domain separator `DS_A = keccak256(abi.encode(TYPE_HASH, NAME_HASH, VERSION_HASH, 42161, address(endpoint)))` is cached in storage.
2. Chain A forks into Chain B (chainId = 99999). The `Endpoint` proxy and storage are cloned on Chain B, but `_cachedDomainSeparator` still holds `DS_A`.
3. A user on Chain A signs a `WithdrawCollateral` transaction. The signature is valid under `DS_A`.
4. An attacker submits the same signed transaction on Chain B via `submitTransactionsChecked`. `validateSignedTx` calls `_hashTypedDataV4(digest)`, which returns `keccak256(abi.encodePacked("\x19\x01", DS_A, digest))` — identical to Chain A — because the draft implementation returns the cached `DS_A` regardless of `block.chainid`.
5. `verifier.validateSignature` recovers the original signer and accepts the signature. The withdrawal executes on Chain B, draining the user's collateral a second time. [9](#0-8)

### Citations

**File:** core/contracts/EndpointTx.sol (L5-5)
```text
import "@openzeppelin/contracts-upgradeable/utils/cryptography/draft-EIP712Upgradeable.sol";
```

**File:** core/contracts/EndpointTx.sol (L86-106)
```text
    function validateSignedTx(
        bytes32 sender,
        uint64 nonce,
        bytes calldata transaction,
        bytes memory signature,
        bool allowLinkedSigner
    ) internal {
        validateNonce(sender, nonce);
        validateSignature(
            sender,
            _hashTypedDataV4(
                computeDigest(
                    IEndpoint.TransactionType(uint8(transaction[0])),
                    transaction[1:]
                )
            ),
            signature,
            allowLinkedSigner
        );
        requireSubaccount(sender);
    }
```

**File:** core/contracts/EndpointTx.sol (L108-128)
```text
    function validateSignedTx(
        bytes32 sender,
        uint64 nonce,
        bytes calldata transaction,
        IEndpoint.CompactSignature memory signature,
        bool allowLinkedSigner
    ) internal {
        validateNonce(sender, nonce);
        validateCompactSignature(
            sender,
            _hashTypedDataV4(
                computeDigest(
                    IEndpoint.TransactionType(uint8(transaction[0])),
                    transaction[1:]
                )
            ),
            signature,
            allowLinkedSigner
        );
        requireSubaccount(sender);
    }
```

**File:** core/contracts/EndpointTx.sol (L391-412)
```text
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

**File:** core/contracts/Endpoint.sol (L6-6)
```text
import "@openzeppelin/contracts-upgradeable/utils/cryptography/draft-EIP712Upgradeable.sol";
```

**File:** core/contracts/Endpoint.sol (L40-40)
```text
        __EIP712_init("Nado", "0.0.1");
```

**File:** core/contracts/Verifier.sol (L5-5)
```text
import "@openzeppelin/contracts-upgradeable/utils/cryptography/draft-EIP712Upgradeable.sol";
```
