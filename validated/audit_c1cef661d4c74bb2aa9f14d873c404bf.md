### Title
Order Digest Domain Separator Binds to Synthetic `productId`-Derived Address Instead of `address(this)`, Enabling Cross-Deployment Order Replay — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.getDigest` manually constructs an EIP-712 domain separator that places `address(uint160(productId))` — a deterministic near-zero address — in the `verifyingContract` field instead of `address(this)`. This is the direct Nado analog to the external report's unauthenticated cipher mode: just as AES-CBC produces a ciphertext that is not bound to its context (no authentication tag), the order digest here is not bound to the actual contract instance. Any two `OffchainExchange` deployments on the same chain sharing the same `productId` will accept each other's order signatures, and the per-contract `filledAmounts` tracking will not prevent double-fills.

---

### Finding Description

`getDigest` in `OffchainExchange.sol` manually builds the EIP-712 domain separator:

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

For product ID 1 this resolves to `verifyingContract = 0x0000000000000000000000000000000000000001`. This is not the `OffchainExchange` contract address. The EIP-712 standard requires `verifyingContract` to be the address of the contract that will verify the signature, precisely to prevent cross-contract replay. The standard `_hashTypedDataV4` path used by `Endpoint` for all other signed transactions correctly uses `address(this)` via `EIP712Upgradeable`: [2](#0-1) 

`getDigest` is called in two places: `matchOrders` (order fill path) and `createIsolatedSubaccount` (isolated margin path): [3](#0-2) [4](#0-3) 

The `filledAmounts` mapping that enforces per-order fill limits is keyed by this digest: [5](#0-4) [6](#0-5) 

Because the digest is not bound to `address(this)`, two distinct `OffchainExchange` contracts on the same chain with the same `productId` produce identical digests for identical orders. Each contract maintains its own `filledAmounts` state, so the fill guard on contract B starts at zero even if the order was fully filled on contract A.

---

### Impact Explanation

**Corrupted state**: `filledAmounts[digest]` on a second `OffchainExchange` instance starts at zero for an order already fully filled on the first instance. The sequencer of the second exchange can submit the same signed order and fill it again, draining the user's collateral or position beyond the amount they authorized.

**Isolated subaccount margin theft**: `digestToMargin[digest]` on the second contract is also zero. A replayed `createIsolatedSubaccount` call re-deducts margin from the user's parent subaccount: [7](#0-6) 

**User transparency broken**: EIP-712 wallets display `verifyingContract` to users before signing. The displayed address (`0x0000...0001`, `0x0000...0002`, etc.) is not the actual exchange contract, so users cannot verify what contract they are authorizing. This is the direct structural analog to the unauthenticated ciphertext in the external report: the signed artifact is not bound to its intended context.

---

### Likelihood Explanation

The Nado protocol is actively developed on Ink Chain and the monorepo structure supports multiple deployments (`test` and `prod` environments). The protocol already has a modular architecture where `OffchainExchange` is a standalone upgradeable contract. Any scenario where a second `OffchainExchange` is deployed on the same chain — a new market segment, a parallel deployment, a testnet/mainnet fork with the same chain ID, or a non-proxy redeployment — immediately activates the cross-deployment replay path. The signed order requires no modification; the attacker only needs to submit it to the second contract's sequencer. The `onlyEndpoint` guard on `matchOrders` is satisfied by the second exchange's own `Endpoint`: [8](#0-7) 

---

### Recommendation

Replace `address(uint160(productId))` with `address(this)` in the domain separator construction inside `getDigest`. The `productId` binding that prevents cross-product replay should instead be encoded in the struct type string or as a field in the `Order` struct hash, not in the `verifyingContract` slot of the domain separator. Alternatively, use the inherited `_domainSeparatorV4()` from `EIP712Upgradeable` (which already uses `address(this)`) and encode `productId` into the struct hash.

---

### Proof of Concept

1. Protocol deploys `OffchainExchangeA` at `0xAAAA` and `OffchainExchangeB` at `0xBBBB`, both on Ink Mainnet (same `block.chainid`), both listing product ID `1`.
2. User signs an `Order` for product `1` with `amount = 100`. Their wallet shows `verifyingContract = 0x0000...0001` — not `0xAAAA` or `0xBBBB`.
3. Sequencer of `OffchainExchangeA` fills the order fully. `filledAmounts[digest]` on A becomes `100`.
4. Sequencer of `OffchainExchangeB` submits the identical signed order bytes. `getDigest(1, order)` on B produces the same `digest` (same name hash, version hash, chainId, and `address(uint160(1))`).
5. `filledAmounts[digest]` on B is `0`. `_validateOrder` passes. The order is filled again for `100` units.
6. The user's collateral on `OffchainExchangeB` is debited for a trade they did not intend to execute on that contract. [9](#0-8) [10](#0-9)

### Citations

**File:** core/contracts/OffchainExchange.sol (L30-30)
```text
    mapping(bytes32 => int128) public filledAmounts;
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

**File:** core/contracts/OffchainExchange.sol (L410-468)
```text
    function _validateOrder(
        CallState memory callState,
        MarketInfo memory,
        IEndpoint.SignedOrder memory signedOrder,
        bytes32 orderDigest,
        bool isTaker,
        address linkedSigner
    ) internal view returns (bool) {
        if ((signedOrder.order.appendix & 255) != orderVersion()) {
            return false;
        }
        if (signedOrder.order.sender == X_ACCOUNT) {
            return true;
        }
        IEndpoint.Order memory order = signedOrder.order;
        if (isTaker) {
            if (_isMakerOnly(order.appendix)) {
                return false;
            }
        } else {
            if (_isTakerOnly(order.appendix)) {
                return false;
            }
        }

        int128 filledAmount = filledAmounts[orderDigest];
        order.amount -= filledAmount;

        if (_isReduceOnly(order.appendix)) {
            int128 amount = callState.isPerp
                ? callState
                    .perp
                    .getBalance(callState.productId, order.sender)
                    .amount
                : callState
                    .spot
                    .getBalance(callState.productId, order.sender)
                    .amount;
            if ((order.amount > 0) == (amount > 0)) {
                order.amount = 0;
            } else if (order.amount > 0) {
                order.amount = MathHelper.min(order.amount, -amount);
            } else if (order.amount < 0) {
                order.amount = MathHelper.max(order.amount, -amount);
            }
        }

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

**File:** core/contracts/OffchainExchange.sol (L631-635)
```text
    function matchOrders(IEndpoint.MatchOrdersWithSigner calldata txn)
        external
        onlyEndpoint
    {
        CallState memory callState = _getCallState(txn.matchOrders.productId);
```

**File:** core/contracts/OffchainExchange.sol (L653-672)
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
        );
```

**File:** core/contracts/OffchainExchange.sol (L831-840)
```text
        if (taker.order.sender != X_ACCOUNT) {
            filledAmounts[ordersInfo.taker.digest] += ordersInfo
                .taker
                .amountDelta;
        }
        if (maker.order.sender != X_ACCOUNT) {
            filledAmounts[ordersInfo.maker.digest] += ordersInfo
                .maker
                .amountDelta;
        }
```

**File:** core/contracts/OffchainExchange.sol (L1008-1008)
```text
        bytes32 digest = getDigest(txn.productId, txn.order);
```

**File:** core/contracts/OffchainExchange.sol (L1074-1087)
```text
        int128 margin = int128(_isolatedMargin(txn.order.appendix));
        if (margin > 0) {
            digestToMargin[digest] = margin;
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                txn.order.sender,
                -margin
            );
            spotEngine.updateBalance(
                QUOTE_PRODUCT_ID,
                newIsolatedSubaccount,
                margin
            );
        }
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
