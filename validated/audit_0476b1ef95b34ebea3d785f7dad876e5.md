### Title
Wrong `verifyingContract` Reference in EIP-712 Domain Separator Allows Cross-Deployment Order Replay — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.getDigest` constructs an EIP-712 domain separator using `address(uint160(productId))` as the `verifyingContract` field instead of `address(this)`. This is a direct analog to the reported bug: the wrong reference is used in a cryptographic validation, causing signed orders to be unbound from the specific contract instance. Any second `OffchainExchange` deployment on the same chain with the same `productId` and `chainId` produces an identical domain separator, enabling cross-deployment signature replay and unauthorized order execution against user balances.

---

### Finding Description

In `OffchainExchange.sol`, the `getDigest` function manually constructs a domain separator:

```solidity
bytes32 private constant _TYPE_HASH =
    keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );
``` [1](#0-0) 

```solidity
bytes32 domainSeparator = keccak256(
    abi.encode(
        _TYPE_HASH,
        _EIP712NameHash(),
        _EIP712VersionHash(),
        block.chainid,
        address(uint160(productId))   // ← WRONG: should be address(this)
    )
);
``` [2](#0-1) 

The `_TYPE_HASH` explicitly declares a `verifyingContract` field, which EIP-712 defines as the address of the contract that will verify the signature. However, the value supplied is `address(uint160(productId))` — a near-zero address derived from the product ID (e.g., `0x0000…0001` for `productId = 1`), not `address(this)`. This is the wrong reference: the type definition promises contract-binding, but the actual value used is a meaningless constant that is identical across every `OffchainExchange` deployment sharing the same `productId` and `chainId`.

The digest produced by `getDigest` is then used directly in `_validateOrder` to verify the user's signature:

```solidity
_checkSignature(
    order.sender,
    orderDigest,
    linkedSigner,
    signedOrder.signature
)
``` [3](#0-2) 

Because `orderDigest` is identical across all deployments with the same `productId`/`chainId`, a signature produced for one deployment is cryptographically valid on any other.

---

### Impact Explanation

If a second `OffchainExchange` (e.g., a new version, a parallel pool, or a redeployment) is live on the same chain with the same `productId`, a malicious sequencer or attacker with access to a second `Endpoint` can replay any previously signed order. The `filledAmounts` mapping starts empty on the new deployment, so the replay-protection check does not fire. The replayed order executes `_updateBalances` against the victim's subaccount in the new deployment's clearinghouse, draining their collateral without their consent. [4](#0-3) 

The corrupted state delta is: victim's spot/perp balance in the replayed deployment is decremented by the full order amount, while the attacker's counterpart subaccount receives the corresponding credit.

---

### Likelihood Explanation

The Nado protocol uses upgradeable proxy infrastructure (`ProxyManager.sol`), and the codebase shows evidence of versioned deployments (`"Nado", "0.0.1"`). Any upgrade that deploys a new `OffchainExchange` address — or any parallel pool deployment — on the same chain immediately activates this replay surface. The sequencer role, which submits `matchOrders` transactions, is a realistic attacker position given the off-chain matching architecture. The attack requires no special privileges beyond access to previously broadcast signed orders (which are observable on-chain or off-chain via the order book).

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
        address(this)   // correct: binds to this specific contract instance
    )
);
``` [5](#0-4) 

This ensures every signed order is cryptographically bound to the exact contract address that will execute it, preventing cross-deployment replay.

---

### Proof of Concept

1. User signs an order for `productId = 1` on `OffchainExchange_A` (deployed at address `0xAAAA…`). The domain separator is `keccak256(abi.encode(_TYPE_HASH, nameHash, versionHash, chainId, address(0x0000…0001)))`.
2. A second `OffchainExchange_B` is deployed at `0xBBBB…` on the same chain (same `chainId`, same `productId = 1`). Its domain separator is **identical**: `keccak256(abi.encode(_TYPE_HASH, nameHash, versionHash, chainId, address(0x0000…0001)))`.
3. The attacker (malicious sequencer) submits the user's signed order to `Endpoint_B`, which calls `OffchainExchange_B.matchOrders`.
4. `_validateOrder` calls `getDigest` on `OffchainExchange_B`, producing the same digest as on `OffchainExchange_A`. `_checkSignature` recovers the user's address and returns `true`.
5. `filledAmounts[digest]` on `OffchainExchange_B` is `0` (fresh deployment), so the order is not considered filled.
6. `_updateBalances` executes, debiting the victim's subaccount and crediting the attacker's counterpart subaccount in `OffchainExchange_B`'s clearinghouse — draining the victim's collateral without their knowledge or consent. [6](#0-5)

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

**File:** core/contracts/OffchainExchange.sol (L457-465)
```text
        return
            ((order.priceX18 > 0) || _isTWAP(order.appendix)) &&
            (signedOrder.order.sender == N_ACCOUNT ||
                _checkSignature(
                    order.sender,
                    orderDigest,
                    linkedSigner,
                    signedOrder.signature
                )) &&
```

**File:** core/contracts/OffchainExchange.sol (L811-824)
```text
        _updateBalances(
            callState,
            market.quoteId,
            taker.order.sender,
            ordersInfo.taker.amountDelta,
            ordersInfo.taker.quoteDelta
        );
        _updateBalances(
            callState,
            market.quoteId,
            maker.order.sender,
            ordersInfo.maker.amountDelta,
            ordersInfo.maker.quoteDelta
        );
```
