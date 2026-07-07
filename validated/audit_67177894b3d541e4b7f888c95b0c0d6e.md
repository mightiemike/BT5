### Title
Order Digest Domain Separator Uses Product ID as Verifying Contract Instead of `address(this)`, Enabling Cross-Deployment Signature Replay — (`File: core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.getDigest()` constructs a custom EIP-712 domain separator that substitutes `address(uint160(productId))` for the verifying contract address instead of `address(this)`. Because product IDs are small integers, the "verifying contract" field resolves to a near-zero address (e.g., `0x0000…0001`) rather than the actual contract. While `block.chainid` is present, the missing real contract address means order signatures are not bound to a specific `OffchainExchange` deployment. Any second Nado deployment on the same chain with the same product IDs produces an identical digest for the same order, and the per-contract `filledAmounts` storage provides no cross-deployment protection.

---

### Finding Description

`OffchainExchange.getDigest()` manually constructs an EIP-712 domain separator:

```solidity
bytes32 domainSeparator = keccak256(
    abi.encode(
        _TYPE_HASH,
        _EIP712NameHash(),
        _EIP712VersionHash(),
        block.chainid,
        address(uint160(productId))   // ← NOT address(this)
    )
);
``` [1](#0-0) 

`_TYPE_HASH` is the standard EIP-712 domain type hash copied from `EIP712Upgradeable`: [2](#0-1) 

The struct hash covers `sender`, `priceX18`, `amount`, `expiration`, `nonce`, and `appendix` — none of which encode the contract address: [3](#0-2) 

The resulting digest is stored in `filledAmounts` to track fill state, but this mapping is per-contract storage: [4](#0-3) 

For contrast, the user-transaction path in `EndpointTx.validateSignedTx()` correctly uses `_hashTypedDataV4()` (which internally uses `address(this)` via the inherited `EIP712Upgradeable` domain separator initialized with `__EIP712_init("Nado", "0.0.1")`): [5](#0-4) [6](#0-5) 

Order matching bypasses this path entirely and relies solely on `getDigest()`.

---

### Impact Explanation

If Nado deploys a second `OffchainExchange` instance on the same chain — whether as an upgrade, a parallel market, or a fork — both instances produce **identical digests** for the same order parameters, because:

- `block.chainid` is the same (same chain)
- `address(uint160(productId))` is the same (same product ID integer)
- `_EIP712NameHash()` / `_EIP712VersionHash()` are the same (same contract code)

An attacker observing a user's signed order on deployment A can submit it to deployment B. Deployment B's `filledAmounts` mapping has no record of the fill on A, so the order passes signature validation and executes. This causes an unauthorized trade against the user's collateral on the second deployment, with no action required from the user beyond having signed a legitimate order on the first deployment.

The same applies to any protocol that forks Nado and deploys on the same chain with the same name/version/product IDs.

---

### Likelihood Explanation

Nado is described as targeting Ink Mainnet and Ink Sepolia. Protocol upgrades that redeploy `OffchainExchange` are a routine operational event. Any such redeployment immediately creates the replay surface. The attacker's only requirement is to observe a signed order on the original deployment (trivially available from on-chain calldata) and replay it on the new deployment before the user's nonce is consumed there.

---

### Recommendation

Replace `address(uint160(productId))` with `address(this)` in the domain separator construction inside `getDigest()`:

```solidity
bytes32 domainSeparator = keccak256(
    abi.encode(
        _TYPE_HASH,
        _EIP712NameHash(),
        _EIP712VersionHash(),
        block.chainid,
        address(this)   // bind to this specific contract instance
    )
);
```

Alternatively, remove the manual domain separator construction entirely and use the inherited `_domainSeparatorV4()` from `EIP712Upgradeable`, which already encodes `block.chainid` and `address(this)` correctly.

---

### Proof of Concept

1. User signs an order on `OffchainExchange` deployment A (productId = 1, chain = Ink Mainnet). The digest is:
   ```
   keccak256(domainSep(chainid=57073, verifier=0x0000…0001) || structHash(order))
   ```
2. Nado upgrades and redeploys `OffchainExchange` as deployment B on the same chain with the same product IDs.
3. Attacker extracts the user's signature from deployment A's transaction calldata.
4. Attacker calls `submitTransactions` on `Endpoint` pointing to deployment B, submitting a `MatchOrders` transaction with the user's original order and signature.
5. `OffchainExchange.getDigest()` on deployment B produces the **same digest** (same `chainid`, same `address(uint160(1))`).
6. `_checkSignature()` recovers the user's address and accepts the signature.
7. `filledAmounts[digest]` on deployment B is zero — the order executes in full, debiting the user's collateral on deployment B without their consent. [7](#0-6)

### Citations

**File:** core/contracts/OffchainExchange.sol (L30-30)
```text
    mapping(bytes32 => int128) public filledAmounts;
```

**File:** core/contracts/OffchainExchange.sol (L98-100)
```text
    // copied from EIP712Upgradeable
    bytes32 private constant _TYPE_HASH =
        keccak256(
```

**File:** core/contracts/OffchainExchange.sol (L296-321)
```text
        string
            memory structType = "Order(bytes32 sender,int128 priceX18,int128 amount,uint64 expiration,uint64 nonce,uint128 appendix)";

        bytes32 structHash = keccak256(
            abi.encode(
                keccak256(bytes(structType)),
                order.sender,
                order.priceX18,
                order.amount,
                order.expiration,
                order.nonce,
                order.appendix
            )
        );

        bytes32 domainSeparator = keccak256(
            abi.encode(
                _TYPE_HASH,
                _EIP712NameHash(),
                _EIP712VersionHash(),
                block.chainid,
                address(uint160(productId))
            )
        );

        return ECDSAUpgradeable.toTypedDataHash(domainSeparator, structHash);
```

**File:** core/contracts/OffchainExchange.sol (L332-343)
```text
    function _checkSignature(
        bytes32 subaccount,
        bytes32 digest,
        address linkedSigner,
        bytes memory signature
    ) internal view virtual returns (bool) {
        address signer = ECDSA.recover(digest, signature);
        return
            (signer != address(0)) &&
            (signer == address(uint160(bytes20(subaccount))) ||
                signer == linkedSigner);
    }
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

**File:** core/contracts/Endpoint.sol (L40-40)
```text
        __EIP712_init("Nado", "0.0.1");
```
