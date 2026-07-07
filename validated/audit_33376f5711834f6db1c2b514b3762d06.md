### Title
Order Signature Domain Separator Uses `productId` as `verifyingContract` Instead of `address(this)`, Enabling Cross-Deployment Replay — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.getDigest()` constructs a custom EIP-712 domain separator that sets the `verifyingContract` field to `address(uint160(productId))` — a near-zero address derived from the integer product ID — rather than `address(this)`. Because the domain separator does not bind to the actual contract address, order signatures are valid on any deployment of the Nado protocol on the same chain that shares the same `productId`, enabling cross-deployment signature replay.

---

### Finding Description

In `OffchainExchange.getDigest()`, the domain separator is manually constructed as follows:

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

The EIP-712 standard requires `verifyingContract` to be the address of the contract that will verify the signature, so that signatures are cryptographically bound to a specific deployed instance. Here, `address(uint160(productId))` evaluates to a near-zero address (e.g., `0x0000...0001` for `productId = 1`), which is identical across every deployment of the protocol on the same chain.

By contrast, the `Endpoint` contract correctly uses `_hashTypedDataV4()` (which internally uses `address(this)`) when validating user-signed transactions such as `WithdrawCollateral`, `LinkSigner`, and `LiquidateSubaccount`:

```solidity
_hashTypedDataV4(
    computeDigest(
        IEndpoint.TransactionType(uint8(transaction[0])),
        transaction[1:]
    )
)
``` [2](#0-1) 

The `OffchainExchange` is initialized with `__EIP712_init("Nado", "0.0.1")` but then bypasses the inherited `_hashTypedDataV4` helper entirely for order digests, constructing its own domain separator with the wrong `verifyingContract`. [3](#0-2) 

The resulting digest is used directly in `_validateOrder` via `_checkSignature`:

```solidity
_checkSignature(
    order.sender,
    orderDigest,
    linkedSigner,
    signedOrder.signature
)
``` [4](#0-3) 

---

### Impact Explanation

If the Nado protocol is redeployed on the same chain — for example, a new `OffchainExchange` is deployed at a different address as part of a protocol upgrade, or a parallel deployment exists — all previously signed orders remain cryptographically valid on the new deployment. The `filledAmounts` mapping on the new deployment starts empty, so every previously signed order appears unfilled and is immediately replayable.

An attacker (or the sequencer in a compromised state) can submit a stale order signed by a user against the new deployment. The signature check passes because the domain separator does not include the actual contract address. The order executes, mutating the user's spot or perp balances via `_updateBalances` without the user's consent. [5](#0-4) 

The corrupted state is: `filledAmounts[digest]` incremented on the new deployment, and the user's `spotEngine` or `perpEngine` balance decremented by the replayed trade amount.

---

### Likelihood Explanation

The Nado protocol uses an upgradeable proxy system (`ProxyManager`). If the `OffchainExchange` is ever redeployed at a new address (rather than upgraded in-place at the same proxy address), the vulnerability becomes immediately exploitable for any order ever signed by any user. Additionally, if multiple Nado deployments coexist on the same chain (e.g., a staging and production environment, or a v1 and v2 with different proxy addresses), cross-deployment replay is possible without any redeployment event. The attacker-controlled entry path is the sequencer submitting a `MatchOrders` or `MatchOrdersWithAmount` transaction containing a replayed signed order. [6](#0-5) 

---

### Recommendation

Replace `address(uint160(productId))` with `address(this)` in the domain separator construction inside `getDigest()`, or replace the manual domain separator construction entirely with the inherited `_hashTypedDataV4(structHash)` helper, which correctly uses `address(this)` as `verifyingContract`:

```solidity
// Before (vulnerable):
bytes32 domainSeparator = keccak256(
    abi.encode(
        _TYPE_HASH,
        _EIP712NameHash(),
        _EIP712VersionHash(),
        block.chainid,
        address(uint160(productId))   // wrong
    )
);
return ECDSAUpgradeable.toTypedDataHash(domainSeparator, structHash);

// After (fixed):
return _hashTypedDataV4(structHash);
```

Note: if `productId` is intentionally included in the domain to scope signatures per-market, it should be encoded in the struct type itself (as a field of the `Order` struct), not in the `verifyingContract` field of the domain separator.

---

### Proof of Concept

1. User signs an `Order` for `productId = 1` on Nado deployment A (`OffchainExchange` at address `0xAAAA`). The domain separator's `verifyingContract` is `address(uint160(1))` = `0x0000...0001`.

2. Nado is redeployed: a new `OffchainExchange` is deployed at address `0xBBBB`. The domain separator for `productId = 1` is still `address(uint160(1))` = `0x0000...0001` — identical to deployment A.

3. On deployment B, `filledAmounts[digest]` is zero (fresh state).

4. An attacker submits the user's old signed order to the sequencer targeting deployment B. The sequencer calls `matchOrders` on the new `OffchainExchange`.

5. `getDigest` produces the same `digest` as on deployment A. `_checkSignature` recovers the user's address and returns `true`.

6. `_validateOrder` passes. `_updateBalances` executes the trade, draining the user's balance on deployment B without their consent. [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/OffchainExchange.sol (L250-250)
```text
        __EIP712_init("Nado", "0.0.1");
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

**File:** core/contracts/OffchainExchange.sol (L459-465)
```text
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

**File:** core/contracts/EndpointTx.sol (L495-533)
```text
        } else if (txType == IEndpoint.TransactionType.MatchOrders) {
            IEndpoint.MatchOrders memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.MatchOrders)
            );
            requireSubaccount(txn.taker.order.sender);
            requireSubaccount(txn.maker.order.sender);

            IEndpoint.MatchOrdersWithSigner memory txnWithSigner = IEndpoint
                .MatchOrdersWithSigner({
                    matchOrders: txn,
                    takerLinkedSigner: getLinkedSignerOrNlpSigner(
                        txn.taker.order.sender
                    ),
                    makerLinkedSigner: getLinkedSignerOrNlpSigner(
                        txn.maker.order.sender
                    ),
                    takerAmountDelta: 0
                });
            IOffchainExchange(offchainExchange).matchOrders(txnWithSigner);
        } else if (txType == IEndpoint.TransactionType.MatchOrdersWithAmount) {
            IEndpoint.MatchOrdersWithAmount memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.MatchOrdersWithAmount)
            );
            requireSubaccount(txn.matchOrders.taker.order.sender);
            requireSubaccount(txn.matchOrders.maker.order.sender);
            IEndpoint.MatchOrdersWithSigner memory txnWithSigner = IEndpoint
                .MatchOrdersWithSigner({
                    matchOrders: txn.matchOrders,
                    takerLinkedSigner: getLinkedSignerOrNlpSigner(
                        txn.matchOrders.taker.order.sender
                    ),
                    makerLinkedSigner: getLinkedSignerOrNlpSigner(
                        txn.matchOrders.maker.order.sender
                    ),
                    takerAmountDelta: txn.takerAmountDelta
                });
            IOffchainExchange(offchainExchange).matchOrders(txnWithSigner);
```
