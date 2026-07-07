### Title
Order Digest Domain Separator Uses Fake `productId`-Derived Address Instead of `address(this)`, Enabling Cross-Contract Order Replay — (`File: core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.getDigest()` constructs an EIP-712 domain separator where the `verifyingContract` field is set to `address(uint160(productId))` — a deterministic fake address derived from the product ID — rather than `address(this)`. This means the domain separator is not bound to the specific `OffchainExchange` contract instance. Any two `OffchainExchange` deployments on the same chain with the same EIP-712 name/version and the same `productId` will produce identical digests for identical orders, enabling cross-contract order signature replay.

---

### Finding Description

In `OffchainExchange.getDigest()`, the domain separator is constructed as:

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

The EIP-712 standard requires the `verifyingContract` field to be `address(this)` so that a signature is cryptographically bound to the specific contract that will consume it. By substituting `address(uint160(productId))`, the domain separator becomes a function of only `(chainId, name, version, productId)` — entirely independent of which `OffchainExchange` contract instance is validating the signature.

This is structurally identical to the reported ERC1271 bug: just as the Clave `isValidSignature()` forwarded a raw `signedHash` without binding it to `address(this)`, Nado's `getDigest()` produces a digest that is not bound to the actual exchange contract address.

The digest is used directly in `_checkSignature()` for order validation:

```solidity
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
``` [2](#0-1) 

And in `createIsolatedSubaccount()`, the same digest is used to gate isolated subaccount creation: [3](#0-2) 

The `filledAmounts` mapping, which tracks how much of an order has been filled and prevents double-fills, is keyed by this digest and is local to each `OffchainExchange` instance: [4](#0-3) 

This means the same order signature, once fully consumed on one `OffchainExchange` instance, can be replayed on a second instance because `filledAmounts` state is not shared.

Contrast this with the main `EndpointTx` transaction path, which correctly uses `_hashTypedDataV4()` (which internally uses `address(this)` in its domain separator) before passing to `verifier.validateSignature()`: [5](#0-4) 

The order-matching path in `OffchainExchange` does not benefit from this protection.

---

### Impact Explanation

If a second `OffchainExchange` instance is deployed on the same chain with the same EIP-712 name/version (e.g., during a contract upgrade where the old instance is not immediately decommissioned, or in a multi-instance deployment), and both instances are connected to the same `Clearinghouse`:

- An order signature that was already fully filled (`filledAmounts[digest] == order.amount`) on instance A has `filledAmounts[digest] == 0` on instance B.
- The attacker (or a malicious sequencer) submits the same signed order bytes to instance B.
- The order passes `_checkSignature()` (same digest, same valid signature) and `filledAmounts` check on B.
- The subaccount's collateral balance in the shared `Clearinghouse` is debited a second time for the same signed intent, corrupting the user's balance state.

For `createIsolatedSubaccount()`, the `digestToSubaccount` mapping on instance B is also empty for the replayed digest, so the isolated subaccount creation check is bypassed and a new isolated subaccount is created against the user's will.

---

### Likelihood Explanation

The exploit requires two active `OffchainExchange` instances sharing the same `Clearinghouse` on the same chain. This is a realistic scenario during protocol upgrades (proxy re-pointing or parallel deployment) or if the protocol runs multiple exchange instances for different market segments. The protocol's use of a proxy/upgrade pattern (`BaseProxyManager.sol`) makes this scenario plausible. The structural flaw is present in every deployment regardless of whether a second instance currently exists. [6](#0-5) 

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
        address(this)   // bind to this specific OffchainExchange instance
    )
);
```

Alternatively, use the inherited `_hashTypedDataV4(structHash)` from `EIP712Upgradeable` directly, which already handles the domain separator correctly with `address(this)`.

---

### Proof of Concept

1. Deploy `OffchainExchange` v1 at address `0xAAAA` and v2 at address `0xBBBB`, both initialized with the same EIP-712 name/version and both pointing to the same `Clearinghouse`.
2. User signs an order: `Order(sender=alice_subaccount, priceX18=P, amount=A, expiration=E, nonce=N, appendix=0)` for `productId=1`.
3. Sequencer submits the order to v1. `getDigest(1, order)` on v1 produces digest `D` (using `address(uint160(1))` as verifying contract, not `0xAAAA`). Order fills. `filledAmounts[D] = A` on v1.
4. Sequencer (or attacker with access to the signed bytes) submits the identical signed order to v2. `getDigest(1, order)` on v2 also produces digest `D` (same formula, same inputs). `filledAmounts[D] = 0` on v2. Signature check passes. Order fills again.
5. Alice's subaccount balance in the shared `Clearinghouse` is debited twice for a single signed authorization.

### Citations

**File:** core/contracts/OffchainExchange.sol (L30-30)
```text
    mapping(bytes32 => int128) public filledAmounts;
```

**File:** core/contracts/OffchainExchange.sol (L311-319)
```text
        bytes32 domainSeparator = keccak256(
            abi.encode(
                _TYPE_HASH,
                _EIP712NameHash(),
                _EIP712VersionHash(),
                block.chainid,
                address(uint160(productId))
            )
        );
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

**File:** core/contracts/OffchainExchange.sol (L1008-1020)
```text
        bytes32 digest = getDigest(txn.productId, txn.order);
        if (digestToSubaccount[digest] != bytes32(0)) {
            return digestToSubaccount[digest];
        }
        require(
            _checkSignature(
                txn.order.sender,
                digest,
                linkedSigner,
                txn.signature
            ),
            ERR_INVALID_SIGNATURE
        );
```

**File:** core/contracts/EndpointTx.sol (L94-104)
```text
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
```

**File:** core/contracts/BaseProxyManager.sol (L1-1)
```text
// SPDX-License-Identifier: GPL-2.0-or-later
```
