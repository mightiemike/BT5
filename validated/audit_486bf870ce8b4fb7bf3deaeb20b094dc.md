### Title
EIP-712 Domain Separator Uses Incorrect `verifyingContract` in `OffchainExchange.getDigest`, Enabling Cross-Deployment Order Replay — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.getDigest` computes the EIP-712 domain separator using `address(uint160(productId))` — a tiny address derived from the product ID — as the `verifyingContract` field instead of `address(this)`. Order signatures are not bound to the specific contract instance. If the `OffchainExchange` is ever redeployed to a new address, all historical order signatures remain valid on the new deployment, enabling replay of previously-cancelled orders.

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
        address(uint160(productId))   // ← wrong value
    )
);
``` [1](#0-0) 

EIP-712 requires `verifyingContract` to be the address of the contract that will verify the signature. `address(uint160(productId))` is not the contract address — for product ID `1` it resolves to `0x0000000000000000000000000000000000000001`, a meaningless address. The actual `OffchainExchange` contract address is never committed to in the domain separator.

This is structurally identical to the Bitcoin sighash bug: in both cases, a hash that is supposed to commit to specific context (previous output script/amount in Bitcoin; verifying contract address in EIP-712) instead uses an incorrect placeholder value. The `_checkSignature` function that consumes this digest recovers the signer against the incorrectly-bound digest:

```solidity
function _checkSignature(
    bytes32 subaccount,
    bytes32 digest,
    address linkedSigner,
    bytes memory signature
) internal view virtual returns (bool) {
    address signer = ECDSA.recover(digest, signature);
    ...
}
``` [2](#0-1) 

Because the domain separator is identical across any two `OffchainExchange` deployments that share the same `productId`, `chainId`, name, and version, a signature produced for one deployment is cryptographically valid on any other.

The `Order` struct that is hashed includes `appendix`, `nonce`, `expiration`, and all order parameters: [3](#0-2) 

These fields are correctly included in the struct hash. The flaw is exclusively in the domain separator's `verifyingContract` field.

---

### Impact Explanation

Order cancellation state is tracked in the `filledAmounts` mapping inside `OffchainExchange`'s storage. When the contract is redeployed to a new address (e.g., during a protocol migration or emergency patch), this mapping is reset to zero. Because the domain separator does not commit to the contract address, every historical order signature — including those for fully-filled or explicitly-cancelled orders — becomes valid again on the new deployment.

An attacker who observed old signed orders (e.g., from on-chain calldata or mempool) can submit them to the new deployment via `matchOrders` or `matchOrdersWithAmount`. The signature verification passes, the `filledAmounts` check passes (mapping is empty), and the trade executes. This directly corrupts user position balances and the `filledAmounts` accounting state.

---

### Likelihood Explanation

Medium. The attack requires a redeployment of `OffchainExchange` to a new address. The protocol's `ProxyManager` architecture explicitly supports contract upgrades and migrations. Any migration that changes the `OffchainExchange` address — a normal operational event for an upgradeable protocol — exposes the full history of signed orders to replay. No privileged access is required to execute the replay itself; any unprivileged caller can submit the replayed transaction through the `Endpoint`'s `submitTransactions` path.

---

### Recommendation

Replace `address(uint160(productId))` with `address(this)` in the domain separator:

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

To preserve product-specific signing (preventing cross-product replay), include `productId` as an additional field in the struct type and struct hash rather than in the domain separator.

---

### Proof of Concept

1. User signs an order for product 1 on `OffchainExchange` instance A (address `0xAAAA`). The domain separator commits to `address(uint160(1))` = `0x0000...0001`, not `0xAAAA`.
2. The order is fully filled; `filledAmounts[orderDigest]` equals the full order amount on instance A.
3. The protocol migrates: `OffchainExchange` is redeployed to instance B (address `0xBBBB`). `filledAmounts` is empty on instance B.
4. Attacker submits the original signed order to instance B via `matchOrders`.
5. `getDigest` on instance B computes the same domain separator (still uses `address(uint160(1))`), producing the identical `orderDigest`.
6. `_checkSignature` recovers the correct signer and returns `true`.
7. `filledAmounts[orderDigest]` is zero on instance B, so the order passes the fill check.
8. The trade executes, corrupting the user's position against their intent.

### Citations

**File:** core/contracts/OffchainExchange.sol (L296-309)
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
