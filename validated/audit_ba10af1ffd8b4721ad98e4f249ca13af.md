### Title
Incorrect `verifyingContract` in `getDigest()` Domain Separator Enables Cross-Deployment Order Replay - (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.getDigest()` manually constructs an EIP712 domain separator using `address(uint160(productId))` as the `verifyingContract` field instead of `address(this)`. Because `productId` is a small `uint32`, this resolves to a near-zero address (e.g., `address(1)`, `address(2)`) that is entirely unrelated to the actual contract. The domain separator is therefore not bound to the `OffchainExchange` deployment, making signed orders replayable across any redeployment of the contract on the same chain.

---

### Finding Description

In `OffchainExchange.sol`, the `getDigest()` function manually builds a domain separator:

```solidity
bytes32 domainSeparator = keccak256(
    abi.encode(
        _TYPE_HASH,
        _EIP712NameHash(),
        _EIP712VersionHash(),
        block.chainid,
        address(uint160(productId))   // ← wrong: productId cast to address
    )
);
``` [1](#0-0) 

`productId` is a `uint32`, so `address(uint160(productId))` produces a near-zero address like `address(1)` or `address(2)` — not the `OffchainExchange` contract address. This bypasses the OpenZeppelin `EIP712Upgradeable` base that the contract already inherits and initializes with `__EIP712_init("Nado", "0.0.1")`. [2](#0-1) [3](#0-2) 

The correct `verifyingContract` per EIP712 is the address of the contract that verifies the signature — `address(this)` in this case.

The digest produced by `getDigest()` is used directly in `matchOrders()` to validate both taker and maker order signatures: [4](#0-3) 

Signature validation in `_validateOrder()` calls `_checkSignature()` against this digest: [5](#0-4) 

---

### Impact Explanation

Because the domain separator encodes `address(uint160(productId))` instead of `address(this)`, the digest for any given order is **identical across all deployments** of `OffchainExchange` on the same chain for the same `productId`. If the protocol is ever redeployed or upgraded to a new `OffchainExchange` address (a realistic scenario for an upgradeable system), every previously signed order remains cryptographically valid on the new deployment. A trader who signed an order on the old deployment — expecting it to be invalidated upon deprecation — would find that order replayable on the new contract, causing unintended position changes and asset loss.

Additionally, since the `verifyingContract` field is a near-zero address rather than the actual contract, the domain separator provides no binding to the actual verifying contract, violating the core EIP712 guarantee.

---

### Likelihood Explanation

The flaw is present in every order digest computed by the live contract. Exploitation requires a redeployment event (upgrade, migration, or protocol restart), which is a realistic operational scenario for an upgradeable protocol. The sequencer submits `matchOrders` transactions, and replayed order digests would pass `_checkSignature` without any additional attacker capability beyond possessing a previously signed order.

---

### Recommendation

Replace `address(uint160(productId))` with `address(this)` in the domain separator construction inside `getDigest()`. Alternatively, remove the manual domain separator construction entirely and use the inherited OZ `_domainSeparatorV4()` method, which already correctly uses `address(this)`:

```solidity
// Replace manual domainSeparator construction with:
return _hashTypedDataV4(structHash);
```

This is consistent with how `Endpoint.sol` and `Verifier.sol` already use `EIP712Upgradeable` correctly.

---

### Proof of Concept

1. User signs an order for `productId = 1` on `OffchainExchange` at address `0xAAAA...`.
2. The domain separator encodes `verifyingContract = address(1)` (not `0xAAAA...`).
3. Protocol is upgraded; new `OffchainExchange` is deployed at `0xBBBB...`.
4. The new deployment computes the same domain separator for `productId = 1` (still `address(1)`).
5. The sequencer (or a malicious actor with sequencer access) submits the old signed order to the new deployment via `matchOrders`.
6. `_validateOrder` → `_checkSignature` recovers the original signer from the identical digest and accepts the signature.
7. The order executes on the new deployment against the user's intent, modifying their position and balance. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/OffchainExchange.sol (L20-24)
```text
contract OffchainExchange is
    IOffchainExchange,
    EndpointGated,
    EIP712Upgradeable
{
```

**File:** core/contracts/OffchainExchange.sol (L98-102)
```text
    // copied from EIP712Upgradeable
    bytes32 private constant _TYPE_HASH =
        keccak256(
            "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
        );
```

**File:** core/contracts/OffchainExchange.sol (L248-251)
```text
        setEndpoint(_endpoint);

        __EIP712_init("Nado", "0.0.1");
        clearinghouse = IClearinghouse(_clearinghouse);
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

**File:** core/contracts/OffchainExchange.sol (L457-468)
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
            // valid amount
            (order.amount != 0) &&
            !_expired(order.expiration);
```

**File:** core/contracts/OffchainExchange.sol (L631-644)
```text
    function matchOrders(IEndpoint.MatchOrdersWithSigner calldata txn)
        external
        onlyEndpoint
    {
        CallState memory callState = _getCallState(txn.matchOrders.productId);

        OrdersInfo memory ordersInfo;

        MarketInfo memory market = getMarketInfo(callState.productId);
        IEndpoint.SignedOrder memory taker = txn.matchOrders.taker;
        IEndpoint.SignedOrder memory maker = txn.matchOrders.maker;

        // isolated subaccounts cannot be used as sender
        require(
```

**File:** core/contracts/OffchainExchange.sol (L653-671)
```text
        ordersInfo = OrdersInfo(
            OrderInfo({
                digest: getDigest(callState.productId, taker.order),
                sender: taker.order.sender,
                amount: taker.order.amount,
                fee: 0,
                builderFee: 0,
                quoteDelta: 0,
                amountDelta: 0
            }),
            OrderInfo({
                digest: getDigest(callState.productId, maker.order),
                sender: maker.order.sender,
                amount: maker.order.amount,
                fee: 0,
                builderFee: 0,
                quoteDelta: 0,
                amountDelta: 0
            })
```
