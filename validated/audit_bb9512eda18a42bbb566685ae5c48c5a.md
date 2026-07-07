### Title
Order Digest Domain Separator Binds to `address(uint160(productId))` Instead of `address(this)`, Enabling Cross-Contract Order Replay — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.getDigest()` constructs a custom EIP-712 domain separator that uses `address(uint160(productId))` as the `verifyingContract` field instead of `address(this)`. Because the domain separator is not bound to the actual contract address, any signed order is valid on every `OffchainExchange` instance that handles the same `productId` on the same chain. If a second `OffchainExchange` contract is ever deployed (migration, parallel deployment, or protocol upgrade to a new proxy), fully-filled orders from the first contract can be replayed on the second, re-executing trades the user already completed.

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
        address(uint160(productId))   // ← NOT address(this)
    )
);
``` [1](#0-0) 

For `productId = 1`, the `verifyingContract` field resolves to `0x0000000000000000000000000000000000000001` — a constant that is identical across every `OffchainExchange` deployment on the same chain. The EIP-712 standard requires `verifyingContract` to be the address of the contract that will verify the signature, precisely to prevent cross-contract replay. By substituting a product-derived constant, this protection is removed.

Contrast this with the `EndpointTx` / `Verifier` path, which correctly wraps struct hashes with `_hashTypedDataV4`, whose domain separator is computed by OpenZeppelin's `EIP712Upgradeable` and includes `address(this)`: [2](#0-1) 

The `OffchainExchange` inherits `EIP712Upgradeable` but ignores the inherited `_domainSeparatorV4()` / `_hashTypedDataV4()` helpers entirely for order digests, instead building a bespoke separator that omits the real contract address. [3](#0-2) 

Order replay protection within a single contract relies on the `filledAmounts` mapping keyed by `orderDigest`: [4](#0-3) 

Because `filledAmounts` is per-contract state, a digest that is fully consumed on contract A has `filledAmounts[digest] == 0` on contract B, making the order appear fresh and valid.

---

### Impact Explanation

An attacker who has a legitimately signed order (e.g., a large sell order that was already fully matched and settled on the original `OffchainExchange`) can submit the same signed order to a second `OffchainExchange` instance. The second contract will:

1. Recompute the same `orderDigest` (identical domain separator, identical struct hash).
2. Find `filledAmounts[digest] == 0` (fresh state).
3. Pass `_checkSignature` (valid signature, same digest).
4. Execute the trade again, updating balances in `SpotEngine` or `PerpEngine`.

This allows an attacker to re-execute trades, inflate positions, or drain counterparty balances without holding a new valid order. The corrupted state delta is the `SpotEngine`/`PerpEngine` balance of the replayed order's counterparty.

**Impact: High** — direct asset loss through unauthorized balance mutations in the product engines.

---

### Likelihood Explanation

The exploit requires a second `OffchainExchange` contract to be deployed on the same chain with the same `chainId`. This is realistic in several scenarios:

- Protocol migration to a new proxy (new proxy address, same `productId` namespace).
- Parallel deployment for a new market segment or fee structure.
- Testnet/mainnet sharing the same `chainId` (non-standard but possible on private forks).

The attacker does not need any privileged access — only a previously signed order (their own or one obtained from on-chain calldata) and the ability to call the new contract's order-matching entry point.

**Likelihood: Medium** — not immediately exploitable in a single-contract deployment, but becomes a concrete attack the moment a second instance is live.

---

### Recommendation

Replace `address(uint160(productId))` with `address(this)` in the domain separator, or use the inherited `_hashTypedDataV4` helper which already includes `address(this)`:

```solidity
// Before (vulnerable)
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

// After (fixed)
return _hashTypedDataV4(structHash);
// _hashTypedDataV4 uses address(this) internally via EIP712Upgradeable
```

If product-scoped domain separation is intentional (to allow the same order to be valid across multiple contracts for the same product), this design decision must be explicitly documented and all deployment scenarios must be audited for replay risk.

---

### Proof of Concept

1. Deploy two `OffchainExchange` instances (`OX_A`, `OX_B`) on the same chain, both initialized with the same `productId = 1`.
2. User signs an `Order` for `productId = 1` on `OX_A` (e.g., sell 100 units at price P).
3. Sequencer submits the order to `OX_A`; it matches fully. `OX_A.filledAmounts[digest] = 100`.
4. Attacker submits the same `(order, signature)` to `OX_B`.
5. `OX_B.getDigest(1, order)` produces the **identical** `digest` (same `address(uint160(1))`, same `chainId`, same struct hash).
6. `OX_B.filledAmounts[digest] == 0` → order appears unfilled.
7. `_checkSignature` passes (valid signature over the same digest).
8. `OX_B` executes the trade, mutating balances in the shared or parallel `SpotEngine`/`PerpEngine`. [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/OffchainExchange.sol (L20-24)
```text
contract OffchainExchange is
    IOffchainExchange,
    EndpointGated,
    EIP712Upgradeable
{
```

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
