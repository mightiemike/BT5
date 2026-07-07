### Title
Incorrect `verifyingContract` in Order Digest Domain Separator Allows Cross-Deployment Signature Replay - (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.getDigest` constructs an EIP-712 domain separator that uses `address(uint160(productId))` as the `verifyingContract` field instead of `address(this)`. Because the domain separator does not bind to the actual contract address, order signatures are valid across any `OffchainExchange` deployment that shares the same `productId` and `chainId`. Upon a contract upgrade or redeployment, previously signed orders can be replayed against the new contract instance.

---

### Finding Description

In `OffchainExchange.getDigest`, the domain separator is constructed as:

```solidity
bytes32 domainSeparator = keccak256(
    abi.encode(
        _TYPE_HASH,
        _EIP712NameHash(),
        _EIP712VersionHash(),
        block.chainid,
        address(uint160(productId))   // ← productId cast to address, NOT address(this)
    )
);
``` [1](#0-0) 

The EIP-712 specification requires `verifyingContract` to be the address of the contract that will verify the signature. Using `address(uint160(productId))` (e.g., `address(5)` for product 5) instead of `address(this)` means the domain separator is identical across every `OffchainExchange` deployment on the same chain that lists the same product. The `filledAmounts` mapping that tracks consumed order digests is per-contract-instance storage: [2](#0-1) 

On a fresh deployment, `filledAmounts` is empty, so any previously signed order whose digest matches (which it will, because the domain separator is contract-address-independent) passes signature verification and can be filled again.

The signature check path is `_validateOrder` → `_checkSignature`: [3](#0-2) 

`_checkSignature` recovers the signer from the digest produced by `getDigest` and accepts the order if the recovered address matches the subaccount owner or linked signer. No additional binding to the contract instance exists.

---

### Impact Explanation

When `OffchainExchange` is upgraded or redeployed (a realistic operational event for an upgradeable protocol), the new contract starts with an empty `filledAmounts` mapping. Any order a user signed against the old contract produces an identical digest on the new contract because `verifyingContract` is `address(uint160(productId))` in both cases. An attacker who observed the old signed orders (from mempool or on-chain history) can submit them to the sequencer targeting the new contract. The sequencer processes them, causing the user's subaccount to have positions opened or closed without their current consent. This directly corrupts subaccount position state and can cause unintended financial loss (e.g., a previously cancelled or expired-intent order being re-executed at a stale price).

---

### Likelihood Explanation

The Nado protocol uses upgradeable contracts (`EIP712Upgradeable` is imported and initialized). Contract upgrades or redeployments are a normal part of protocol operations. All historical signed orders are publicly observable on-chain. The attacker needs only to extract a prior signed order from transaction history and submit it after a redeployment — no privileged access is required. The `expiration` field in the Order struct provides a time bound, but orders with far-future or zero expirations (if permitted) would remain replayable indefinitely. [4](#0-3) 

---

### Recommendation

Replace `address(uint160(productId))` with `address(this)` in the domain separator construction inside `getDigest`:

```solidity
bytes32 domainSeparator = keccak256(
    abi.encode(
        _TYPE_HASH,
        _EIP712NameHash(),
        _EIP712VersionHash(),
        block.chainid,
        address(this)   // correct: binds signature to this contract instance
    )
);
```

Alternatively, use the inherited `_domainSeparatorV4()` from `EIP712Upgradeable` directly, which already computes the correct domain separator using `address(this)`. [5](#0-4) 

---

### Proof of Concept

1. User Alice signs an `Order` for `productId = 5` on `OffchainExchange` v1 (address `0xAAA`). The domain separator's `verifyingContract` field is `address(uint160(5))` = `0x0000...0005`, not `0xAAA`.
2. The protocol upgrades and deploys `OffchainExchange` v2 at address `0xBBB`. The domain separator for the same order on v2 is also `verifyingContract = 0x0000...0005` — identical to v1.
3. An attacker extracts Alice's signed order from the v1 transaction history.
4. The attacker submits the same signed order to the sequencer referencing v2. `_checkSignature` recovers Alice's address from the digest (which is identical), `filledAmounts[orderDigest]` is `0` on v2, and the order is accepted and executed.
5. Alice's subaccount on v2 now has an unintended position, with real collateral at risk. [6](#0-5)

### Citations

**File:** core/contracts/OffchainExchange.sol (L98-102)
```text
    // copied from EIP712Upgradeable
    bytes32 private constant _TYPE_HASH =
        keccak256(
            "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
        );
```

**File:** core/contracts/OffchainExchange.sol (L291-322)
```text
    function getDigest(uint32 productId, IEndpoint.Order memory order)
        public
        view
        returns (bytes32)
    {
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
    }
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

**File:** core/contracts/OffchainExchange.sol (L435-436)
```text
        int128 filledAmount = filledAmounts[orderDigest];
        order.amount -= filledAmount;
```
