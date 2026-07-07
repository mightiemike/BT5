### Title
No On-Chain Order Cancellation Mechanism — Complete Dependency on Centralized Sequencer for Order Invalidation - (File: `core/contracts/OffchainExchange.sol`, `core/contracts/interfaces/IEndpoint.sol`)

---

### Summary

Nado's off-chain order system has no on-chain cancellation path whatsoever. Unlike the PuttyV2 analog (which at least had a `cancel()` requiring full order parameters), Nado exposes a more severe variant: there is no `CancelOrder` transaction type, no `minimumValidNonce` guard, and no counter mechanism. A maker who has signed an order is entirely dependent on the centralized sequencer to prevent that order from being filled. If the sequencer goes offline or a party obtains a copy of the order book, all outstanding signed orders remain permanently fillable until they expire.

---

### Finding Description

Orders in Nado are EIP-712 signed off-chain. The `Order` struct contains `sender`, `priceX18`, `amount`, `expiration`, `nonce`, and `appendix`. [1](#0-0) 

The `nonce` field in `Order` is a free parameter chosen by the maker at signing time and is baked into the order digest. It is **not** checked against any on-chain per-user state for cancellation purposes. [2](#0-1) 

The `_validateOrder` function — the sole gate before an order is filled — checks only: order version, signature validity, remaining amount (via `filledAmounts`), and expiration. There is no cancellation flag, no minimum-valid-nonce check, and no counter check. [3](#0-2) 

The `filledAmounts` mapping tracks how much of each order digest has been filled. It is only ever incremented; there is no mechanism to mark a digest as cancelled. [4](#0-3) 

The `TransactionType` enum enumerates every supported on-chain operation. There is no `CancelOrder`, `SetMinimumValidNonce`, or `IncrementCounter` entry. [5](#0-4) 

The `nonces` mapping used in `validateNonce` is a transaction-level sequential replay guard for operations like withdrawals and liquidations. It is entirely separate from order-level cancellation and provides no protection here. [6](#0-5) 

---

### Impact Explanation

A maker who has signed an order and submitted it to the sequencer has no on-chain recourse to invalidate it. The only natural expiry is the `expiration` field embedded in the signed order itself — which the maker cannot change after signing. If the sequencer is unavailable, the maker cannot request cancellation. Any party holding a copy of the signed order (e.g., a mirror of the sequencer's order book) can submit it for matching via `matchOrders` at any time before expiration, including after market conditions have moved against the maker. [7](#0-6) 

---

### Likelihood Explanation

Nado explicitly uses a hybrid sequencer model where orders are signed off-chain and stored in the sequencer's database. The sequencer is the sole source of order data for most users. A sequencer outage, data loss, or a malicious actor with a database mirror creates a realistic window for exploitation. The `expiration` field provides a time-bounded upper limit, but orders with long expiration windows remain at risk for their full duration.

---

### Recommendation

Add a `minimumValidNonce` mapping from subaccount to `uint64`, checked inside `_validateOrder`:

```solidity
mapping(bytes32 => uint64) public minimumValidNonce;
```

Add a signed slow-mode transaction type (e.g., `SetMinimumValidNonce`) that allows a maker to atomically invalidate all orders whose `order.nonce` is below the new minimum. Cap the increment to prevent accidental self-lockout (e.g., disallow incrementing by more than `2**32` in a single call). Inside `_validateOrder`, add:

```solidity
if (order.nonce < minimumValidNonce[order.sender]) return false;
```

This mirrors the fix applied to PuttyV2 and allows makers to perform a "red button" bulk cancellation without revealing individual order parameters, and without depending on the sequencer being online.

---

### Proof of Concept

1. Alice signs a limit order with `expiration = block.timestamp + 7 days` and submits it to the Nado sequencer.
2. Bob mirrors the sequencer's order book via the public API.
3. The Nado sequencer goes offline.
4. Market conditions move sharply against Alice's order (e.g., she signed a buy order and the asset price has crashed).
5. Alice cannot cancel her order: there is no `CancelOrder` transaction type, no `minimumValidNonce` she can increment, and no other on-chain path to invalidate the signed message.
6. Bob submits Alice's signed order as the `maker` in a `MatchOrdersWithSigner` call, pairing it with his own taker order at the now-unfavorable price.
7. `_validateOrder` passes: the signature is valid, `filledAmounts[digest]` is zero, and `expiration` has not elapsed.
8. The trade executes at terms Alice would have cancelled, causing a direct asset loss to Alice. [8](#0-7)

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L11-47)
```text
    enum TransactionType {
        LiquidateSubaccount,
        DepositCollateral,
        WithdrawCollateral,
        SpotTick,
        UpdatePrice,
        SettlePnl,
        MatchOrders,
        DepositInsurance,
        ExecuteSlowMode,
        DumpFees,
        PerpTick,
        ManualAssert,
        UpdateProduct, // deprecated
        LinkSigner,
        UpdateFeeTier,
        TransferQuote,
        RebalanceXWithdraw,
        AssertCode,
        WithdrawInsurance,
        CreateIsolatedSubaccount,
        DelistProduct,
        MintNlp,
        BurnNlp,
        MatchOrdersWithAmount,
        UpdateTierFeeRates,
        AddNlpPool,
        UpdateNlpPool,
        DeleteNlpPool,
        AssertProduct,
        CloseIsolatedSubaccount,
        UpdateBuilder,
        ClaimBuilderFee,
        WithdrawCollateralV2,
        ForceRebalanceNlpPool,
        NlpProfitShare
    }
```

**File:** core/contracts/interfaces/IEndpoint.sol (L261-268)
```text
    struct Order {
        bytes32 sender;
        int128 priceX18;
        int128 amount;
        uint64 expiration;
        uint64 nonce;
        uint128 appendix;
    }
```

**File:** core/contracts/OffchainExchange.sol (L30-30)
```text
    mapping(bytes32 => int128) public filledAmounts;
```

**File:** core/contracts/OffchainExchange.sol (L291-310)
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

```

**File:** core/contracts/OffchainExchange.sol (L410-469)
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
    }
```

**File:** core/contracts/OffchainExchange.sol (L631-701)
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
            !RiskHelper.isIsolatedSubaccount(taker.order.sender),
            ERR_INVALID_TAKER
        );
        require(
            !RiskHelper.isIsolatedSubaccount(maker.order.sender),
            ERR_INVALID_MAKER
        );

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
        if (digestToSubaccount[ordersInfo.taker.digest] != bytes32(0)) {
            taker.order.sender = digestToSubaccount[ordersInfo.taker.digest];
        }
        if (digestToSubaccount[ordersInfo.maker.digest] != bytes32(0)) {
            maker.order.sender = digestToSubaccount[ordersInfo.maker.digest];
        }

        require(
            _validateOrder(
                callState,
                market,
                taker,
                ordersInfo.taker.digest,
                true,
                txn.takerLinkedSigner
            ),
            ERR_INVALID_TAKER
        );
        require(
            _validateOrder(
                callState,
                market,
                maker,
                ordersInfo.maker.digest,
                false,
                txn.makerLinkedSigner
            ),
            ERR_INVALID_MAKER
        );
```

**File:** core/contracts/EndpointTx.sol (L72-77)
```text
    function validateNonce(bytes32 sender, uint64 nonce) internal virtual {
        require(
            nonce == nonces[address(uint160(bytes20(sender)))]++,
            ERR_WRONG_NONCE
        );
    }
```
