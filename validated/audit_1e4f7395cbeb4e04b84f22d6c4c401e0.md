### Title
Maker Can Invalidate All Pending Signed Orders by Rotating Linked Signer, Causing Taker Transactions to Fail - (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

A maker who has signed limit orders off-chain using a linked signer can atomically invalidate every one of those pending orders by submitting a `LinkSigner` transaction to rotate the linked signer address. When the sequencer subsequently attempts to execute `matchOrders`, `_checkSignature` evaluates the **current** linked signer at execution time, not the one that was active when the order was signed. All orders signed by the old linked signer fail with `ERR_INVALID_MAKER`, while the taker's side of the trade is wasted.

---

### Finding Description

`OffchainExchange._validateOrder` verifies an order's signature by calling `_checkSignature` with the linked signer that is read **at the moment `matchOrders` is executed**:

```solidity
// OffchainExchange.sol _validateOrder
return
    ...
    (signedOrder.order.sender == N_ACCOUNT ||
        _checkSignature(
            order.sender,
            orderDigest,
            linkedSigner,       // <-- current state, not signing-time state
            signedOrder.signature
        )) &&
    ...
``` [1](#0-0) 

`_checkSignature` accepts the signature only if the recovered signer equals the subaccount owner **or** the current `linkedSigner`:

```solidity
function _checkSignature(...) internal view virtual returns (bool) {
    address signer = ECDSA.recover(digest, signature);
    return
        (signer != address(0)) &&
        (signer == address(uint160(bytes20(subaccount))) ||
            signer == linkedSigner);
}
``` [2](#0-1) 

The `linkedSigner` value is fetched live from storage in `EndpointTx.processTransactionImpl` at the time the sequencer submits the `MatchOrders` batch:

```solidity
takerLinkedSigner: getLinkedSignerOrNlpSigner(txn.taker.order.sender),
makerLinkedSigner: getLinkedSignerOrNlpSigner(txn.maker.order.sender),
``` [3](#0-2) 

`linkedSigners[subaccount]` is written unconditionally by the `LinkSigner` transaction type, both through the sequencer path and through slow mode:

```solidity
// sequencer path
linkedSigners[signedTx.tx.sender] = address(uint160(bytes20(signedTx.tx.signer)));
``` [4](#0-3) 

```solidity
// slow-mode path (no nonce required)
linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
``` [5](#0-4) 

There is no time-lock, no commitment window, and no snapshot of the linked signer embedded in the order digest. The `Order` struct includes `sender`, `priceX18`, `amount`, `expiration`, `nonce`, and `appendix`, but **not** the linked signer address:

```solidity
string memory structType =
    "Order(bytes32 sender,int128 priceX18,int128 amount,uint64 expiration,uint64 nonce,uint128 appendix)";
``` [6](#0-5) 

Because the linked signer is absent from the digest, rotating it does not change any existing digest, yet it silently invalidates every signature produced by the old linked signer.

---

### Impact Explanation

A maker who has placed many resting limit orders signed by linked signer `H` can, at any moment, submit a `LinkSigner` transaction to replace `H` with a fresh address `H'`. Every pending order signed by `H` immediately fails `_checkSignature`. The sequencer's next `matchOrders` batch reverts with `ERR_INVALID_MAKER` for each affected pair. The taker's order is consumed (nonce advanced, gas spent, sequencer slot used) but no trade executes. The maker retains full optionality: it can observe taker flow, decide the trade is unfavorable, rotate the linked signer, and escape the fill — a structural "last-look" advantage unavailable to takers. [7](#0-6) 

---

### Likelihood Explanation

Any maker using a linked signer (a common pattern for hot-wallet trading bots) can trigger this at will with a single signed `LinkSigner` transaction submitted to the sequencer. No privileged access, no oracle manipulation, and no governance action is required. The only cost is the sequencer fee for the `LinkSigner` transaction. Because the sequencer processes transactions in ordered batches, a `LinkSigner` submitted in batch `B` will invalidate all orders that the sequencer attempts to match in batch `B+1` or later. This is a realistic, low-cost griefing and selective-avoidance vector for any active market maker. [8](#0-7) 

---

### Recommendation

1. **Bind the linked signer into the order digest.** Add the linked signer address (or a commitment hash) to the `Order` struct and include it in `getDigest`. This makes each signature valid only for the linked signer that was active at signing time, so rotating the signer does not retroactively invalidate old orders.

2. **Introduce a time-lock on linked signer rotation.** Record the block timestamp when `linkedSigners[subaccount]` is updated and reject `matchOrders` calls that use a linked signer changed within the last `N` blocks. This prevents atomic invalidation of a full order book.

3. **Alternatively, require explicit per-order cancellation.** Rather than allowing implicit invalidation via signer rotation, require makers to submit explicit cancel transactions for each order they wish to withdraw.

---

### Proof of Concept

1. Maker `M` calls `LinkSigner` to set `linkedSigners[M_subaccount] = H` (hot wallet).
2. `M` signs 50 maker orders off-chain using `H`; orders enter the sequencer's order book.
3. Taker `T` submits matching taker orders; the sequencer queues 50 `MatchOrders` transactions for batch `B+1`.
4. `M` observes the taker flow and decides the fills are unfavorable. `M` submits a `LinkSigner` transaction (nonce `k`) to the sequencer setting `linkedSigners[M_subaccount] = H'` (a fresh address with no signing key).
5. Sequencer processes the `LinkSigner` in batch `B`, writing `linkedSigners[M_subaccount] = H'`.
6. Sequencer processes batch `B+1`: for each `MatchOrders`, `getLinkedSignerOrNlpSigner` returns `H'`. `_checkSignature` recovers `H` from the signature, finds `H != H'` and `H != address(M_subaccount)`, returns `false`. `_validateOrder` returns `false`. `matchOrders` reverts with `ERR_INVALID_MAKER`.
7. All 50 taker orders fail. `M` has avoided every fill at zero asset cost. [9](#0-8) [10](#0-9)

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

**File:** core/contracts/OffchainExchange.sol (L680-701)
```text
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

**File:** core/contracts/EndpointTx.sol (L143-157)
```text
    function getLinkedSigner(bytes32 subaccount)
        public
        view
        virtual
        returns (address)
    {
        return
            RiskHelper.isIsolatedSubaccount(subaccount)
                ? linkedSigners[
                    IOffchainExchange(offchainExchange).getParentSubaccount(
                        subaccount
                    )
                ]
                : linkedSigners[subaccount];
    }
```

**File:** core/contracts/EndpointTx.sol (L232-239)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.LinkSigner memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.LinkSigner)
            );
            validateSender(txn.sender, sender);
            requireSubaccount(txn.sender);
            linkedSigners[txn.sender] = address(uint160(bytes20(txn.signer)));
```

**File:** core/contracts/EndpointTx.sol (L503-514)
```text
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
```

**File:** core/contracts/EndpointTx.sol (L576-590)
```text
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.SignedLinkSigner memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedLinkSigner)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            linkedSigners[signedTx.tx.sender] = address(
                uint160(bytes20(signedTx.tx.signer))
            );
```
