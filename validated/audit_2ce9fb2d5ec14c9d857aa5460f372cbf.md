### Title
EIP-712 Domain Separator in `OffchainExchange.getDigest` Uses Product ID as Verifying Contract, Enabling Order Signature Replay on Contract Redeployment — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

The `getDigest` function in `OffchainExchange.sol` constructs an EIP-712 domain separator using `address(uint160(productId))` as the `verifyingContract` field instead of `address(this)`. This is the direct analog to the ERC1271 report's root cause: the domain separator does not bind to the actual verifying contract, so order signatures are replayable on any future `OffchainExchange` deployment that shares the same product IDs and chain ID.

---

### Finding Description

In `OffchainExchange.getDigest`, the domain separator is manually constructed as:

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

The `verifyingContract` field is `address(uint160(productId))` — a deterministic pseudo-address derived from the product ID integer (e.g., product ID `1` → `0x0000000000000000000000000000000000000001`). This is not the actual `OffchainExchange` contract address. The EIP-712 specification requires `verifyingContract` to be the address of the contract that will verify the signature, precisely to prevent replay across different contract instances.

The `_checkSignature` function then uses this digest to validate order signatures:

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

Order fill state is tracked in the `filledAmounts` mapping, which is storage local to the `OffchainExchange` contract instance: [3](#0-2) 

The `offchainExchange` address in `Endpoint` is set at initialization and can be changed by the owner (the protocol is explicitly upgradeable via ERC1967 proxies): [4](#0-3) 

Because the domain separator does not include `address(this)`, the digest produced by `getDigest` for a given order is **identical** across any `OffchainExchange` deployment on the same chain with the same product IDs. A new deployment starts with an empty `filledAmounts` mapping, so any previously signed and fully-filled order can be replayed.

By contrast, the main transaction path in `EndpointTx.validateSignedTx` correctly wraps the struct hash with `_hashTypedDataV4`, which uses the `Endpoint` proxy's domain separator (initialized with `__EIP712_init("Nado", "0.0.1")` and bound to `address(this)`): [5](#0-4) [6](#0-5) 

Order matching via `OffchainExchange` does not go through this protected path — it uses the manually constructed domain separator in `getDigest` directly.

---

### Impact Explanation

If the `OffchainExchange` contract is replaced (e.g., the `offchainExchange` address in `Endpoint` is updated to a new deployment, which is a realistic upgrade scenario), the new contract starts with a fresh `filledAmounts` state. Because the domain separator is identical across deployments (it uses `address(uint160(productId))`, not `address(this)`), every order signature ever signed by a user is valid on the new deployment. An attacker (or a malicious sequencer) can replay any historical order — including fully-filled, expired-but-still-signed, or cancelled orders — causing unauthorized double-execution of trades. The corrupted state delta is `filledAmounts[digest]` being reset to zero on the new deployment, and the resulting asset delta is unauthorized collateral movement via `clearinghouse` settlement of replayed trades.

---

### Likelihood Explanation

The protocol explicitly uses an upgradeable proxy architecture and the `offchainExchange` address is a mutable field in `Endpoint`. A contract upgrade that replaces `OffchainExchange` (rather than upgrading it in-place via the same proxy) is a realistic operational event. The attack requires no special privileges for the replayer beyond submitting orders through the sequencer — the sequencer processes `MatchOrders` transactions that reference order signatures, and any party who observed a historical signed order can resubmit it.

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
        address(this)   // bind to the actual OffchainExchange instance
    )
);
```

Alternatively, use the inherited `_hashTypedDataV4` from `EIP712Upgradeable` (which `OffchainExchange` already inherits) after properly initializing it with `__EIP712_init`, consistent with how `EndpointTx.validateSignedTx` handles all other signed transaction types.

---

### Proof of Concept

1. User signs an `Order` for product ID `1` with nonce `N` and amount `A`. The digest is `D = getDigest(1, order)`, where the domain separator encodes `verifyingContract = 0x0000000000000000000000000000000000000001`.
2. The sequencer submits a `MatchOrders` transaction; the order is fully filled. `filledAmounts[D] = A` on the current `OffchainExchange` at address `OE_v1`.
3. The protocol owner deploys a new `OffchainExchange` at address `OE_v2` and calls `Endpoint.initialize` (or an upgrade path) to point `offchainExchange` to `OE_v2`. On `OE_v2`, `filledAmounts[D] == 0`.
4. An attacker resubmits the original signed order to the sequencer. The sequencer calls `IOffchainExchange(OE_v2).matchOrders(...)`.
5. `OE_v2.getDigest(1, order)` produces the same `D` (same `block.chainid`, same `address(uint160(1))`, same struct hash). `_checkSignature` recovers the user's address and returns `true`. `filledAmounts[D]` is zero, so the fill proceeds.
6. The order is executed again, moving collateral through `clearinghouse` without the user's intent for a second fill. [7](#0-6) [8](#0-7)

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

**File:** core/contracts/Endpoint.sol (L38-46)
```text
    ) external initializer {
        __Ownable_init();
        __EIP712_init("Nado", "0.0.1");
        sequencer = _sequencer;
        clearinghouse = _clearinghouse;
        offchainExchange = _offchainExchange;
        verifier = IVerifier(_verifier);
        sanctions = ISanctionsList(_sanctions);
        endpointTx = _endpointTx;
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

**File:** core/contracts/EndpointTx.sol (L495-514)
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
```
