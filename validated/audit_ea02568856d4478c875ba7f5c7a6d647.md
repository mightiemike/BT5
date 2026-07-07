### Title
EIP-712 Domain Separator Uses `address(uint160(productId))` as `verifyingContract` Instead of `address(this)`, Enabling Cross-Deployment Order Replay — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary
`OffchainExchange.getDigest()` constructs an EIP-712 domain separator that substitutes `address(uint160(productId))` for the `verifyingContract` field instead of `address(this)`. This severs the cryptographic binding between an order signature and the specific contract instance that verifies it. Any future `OffchainExchange` deployment sharing the same product IDs, protocol name, version, and chain ID will accept signatures originally created for the old deployment, because the `filledAmounts` state that prevents double-fills is per-contract and starts empty on a new deployment.

---

### Finding Description

`OffchainExchange.getDigest()` manually constructs the EIP-712 domain separator:

```solidity
bytes32 domainSeparator = keccak256(
    abi.encode(
        _TYPE_HASH,
        _EIP712NameHash(),
        _EIP712VersionHash(),
        block.chainid,
        address(uint160(productId))   // ← BUG: should be address(this)
    )
);
``` [1](#0-0) 

EIP-712 defines the fifth field of the domain separator as `verifyingContract`, whose purpose is to bind the signature to the exact contract that will verify it. By substituting `address(uint160(productId))` — a near-zero address derived from the product ID integer — the domain separator becomes identical across every `OffchainExchange` deployment that shares the same product ID, protocol name (`"Nado"`), version (`"0.0.1"`), and chain ID. [2](#0-1) 

The computed digest is then passed directly into `_validateOrder` → `_checkSignature`, which performs ECDSA recovery against it:

```solidity
ordersInfo.taker.digest = getDigest(callState.productId, taker.order),
...
_validateOrder(..., ordersInfo.taker.digest, true, txn.takerLinkedSigner)
``` [3](#0-2) 

`_checkSignature` accepts the signature if the recovered address matches the subaccount owner or its linked signer:

```solidity
address signer = ECDSA.recover(digest, signature);
return (signer != address(0)) &&
    (signer == address(uint160(bytes20(subaccount))) || signer == linkedSigner);
``` [4](#0-3) 

The only on-chain guard against replaying a previously-filled order is the `filledAmounts` mapping, which is contract-local storage:

```solidity
int128 filledAmount = filledAmounts[orderDigest];
order.amount -= filledAmount;
``` [5](#0-4) 

Because `filledAmounts` is scoped to a single contract instance, a fresh deployment starts with all values at zero, making every previously-filled order appear unfilled.

---

### Impact Explanation

If Nado deploys a new `OffchainExchange` at a different address (a v2 deployment, a migration, or a redeployment after a critical bug fix), every order signature ever created for the old deployment is immediately valid on the new one for the same product IDs. An attacker who holds a counterparty position can submit a victim's old, fully-filled order to the sequencer's order book. The sequencer, which checks `filledAmounts` only on the current contract, sees the order as unfilled and processes it. The victim's collateral is debited a second time for a trade they already completed.

The corrupted state delta is: `spotEngine` or `perpEngine` balance for the victim's subaccount is decremented by the full order amount a second time, constituting direct collateral theft.

---

### Likelihood Explanation

Contract redeployments are a standard operational event for upgradeable protocol infrastructure. The `OffchainExchange` contract inherits `Initializable` and `OwnableUpgradeable`, indicating it is designed to be deployed and initialized, not only upgraded in-place via a proxy. A non-proxy redeployment (e.g., to a new address after a storage layout change or a critical patch) resets `filledAmounts` to zero. Order signatures have explicit `expiration` fields; any order whose expiration has not yet passed at the time of redeployment is immediately replayable. No privileged access is required from the attacker — submitting an order to the sequencer's public order book is the normal user interaction path.

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
``` [1](#0-0) 

This is the standard pattern used by OpenZeppelin's `EIP712Upgradeable`, which `OffchainExchange` already inherits. The contract could alternatively delegate to `_domainSeparatorV4()` from that base class, which already uses `address(this)` correctly.

---

### Proof of Concept

1. User A signs an order: `Order{sender: A, productId: 1, amount: 100, priceX18: P, expiration: T+7days, nonce: N}`. The digest is `D = getDigest(1, order)`, where the domain separator encodes `verifyingContract = address(1)` (not the contract address).
2. The sequencer matches the order on `OffchainExchange_v1`. `filledAmounts[D]` on `OffchainExchange_v1` becomes `100`. User A's collateral is debited.
3. Nado deploys `OffchainExchange_v2` at a new address. Because the domain separator uses `address(uint160(productId))` and not `address(this)`, `getDigest(1, order)` on `OffchainExchange_v2` returns the same `D`. `filledAmounts[D]` on `OffchainExchange_v2` is `0`.
4. The attacker (acting as counterparty) submits User A's original signed order (same bytes, same signature) to the sequencer's order book before expiration `T+7days`.
5. The sequencer calls `matchOrders` on `OffchainExchange_v2`. `_validateOrder` checks `filledAmounts[D] == 0`, signature recovers to User A's address — validation passes.
6. User A's collateral is debited a second time for the same trade. The attacker receives the matched asset delta. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/OffchainExchange.sol (L243-251)
```text
    function initialize(address _clearinghouse, address _endpoint)
        external
        initializer
    {
        __Ownable_init();
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

**File:** core/contracts/OffchainExchange.sol (L653-690)
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
```
