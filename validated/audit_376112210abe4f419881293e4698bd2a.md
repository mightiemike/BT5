### Title
Order Digest Domain Separator Uses Product-Derived Pseudo-Address Instead of Contract Address, Enabling Cross-Deployment Order Replay — (`core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.getDigest` constructs the EIP-712 domain separator with `address(uint160(productId))` as the `verifyingContract` field instead of `address(this)`. Because this pseudo-address is identical across every `OffchainExchange` deployment on the same chain for the same product, a user's signed order is not bound to any specific contract instance. An attacker can replay an unexpired order from one Nado deployment into a second deployment on the same chain, executing trades the user never intended.

---

### Finding Description

In `OffchainExchange.getDigest`, the EIP-712 domain separator is built as:

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

`address(uint160(productId))` is a deterministic pseudo-address derived from the product ID integer — for example, `productId = 1` maps to `0x0000000000000000000000000000000000000001`. This value is identical for every `OffchainExchange` contract ever deployed on the same chain for the same product.

EIP-712 mandates that `verifyingContract` be the address of the contract that will verify the signature, precisely to prevent replay across different contract instances. By substituting a fake address, the domain separator provides no binding to the actual `OffchainExchange` contract address.

The struct being signed is:

```
Order(bytes32 sender,int128 priceX18,int128 amount,uint64 expiration,uint64 nonce,uint128 appendix)
``` [2](#0-1) 

The `nonce` field in `Order` is not a sequential replay-prevention nonce; it is part of the order's identity used to compute the digest key in `filledAmounts`. In a fresh deployment, `filledAmounts` is empty, so any previously-signed order whose digest has not been recorded there is accepted as new. [3](#0-2) 

The `_checkSignature` function used during order matching recovers the signer from the digest and checks it against the subaccount owner or linked signer — it has no additional binding to the contract instance. [4](#0-3) 

---

### Impact Explanation

If a second `OffchainExchange` is deployed on the same chain (e.g., after a protocol upgrade, a migration, or a parallel deployment), any unexpired order signed against the first deployment produces an identical digest in the second deployment. An attacker who observed the original signed order can submit it to the new deployment via the sequencer path (`submitTransactionsChecked` → `MatchOrders` / `MatchOrdersWithAmount`), causing the user's position to be opened or closed without their current consent. [5](#0-4) 

The user's collateral balance in the new deployment is affected: a replayed sell order drains their position; a replayed buy order opens an unwanted leveraged position. Because the order may have been signed at a price that is now stale, the user suffers a direct financial loss proportional to the order size and price drift.

---

### Likelihood Explanation

The precondition is a second `OffchainExchange` deployment on the same chain with the same EIP-712 name/version. This is a realistic scenario during protocol upgrades (Nado uses a proxy/upgrade architecture) or when a parallel environment is stood up. The attacker needs only to have observed a valid, unexpired signed order from the first deployment — these are broadcast to the sequencer and are not secret. No privileged access is required; the attacker submits the replayed transaction through the normal sequencer path or, if the sequencer is cooperative, through `submitSlowModeTransaction`. [6](#0-5) 

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

Alternatively, use the inherited `_hashTypedDataV4(structHash)` from `EIP712Upgradeable`, which automatically uses `address(this)` in the domain separator, consistent with how `EndpointTx.validateSignedTx` handles all other user-signed transaction types. [7](#0-6) 

---

### Proof of Concept

1. Nado v1 is live. User signs an `Order` for `productId = 2`, `priceX18 = P`, `amount = A`, `expiration = T_future`, `nonce = N`. The domain separator's `verifyingContract` field is `address(uint160(2))` = `0x0000000000000000000000000000000000000002`.

2. The order is partially filled or cancelled in v1. The user considers it done.

3. Nado deploys a new `OffchainExchange` (v2) at a different address on the same chain. The `_EIP712NameHash()` and `_EIP712VersionHash()` are the same. `getDigest` for `productId = 2` still produces `verifyingContract = 0x0000...0002`.

4. The attacker calls `getDigest(2, order)` on v2 and obtains the same digest as in v1.

5. The attacker submits a `MatchOrders` transaction to v2 using the user's original signature. `_checkSignature` recovers the correct signer and returns `true`. [4](#0-3) 

6. The order executes in v2. The user's position is mutated without their consent, and they suffer a financial loss if the market price has moved since the original signing.

### Citations

**File:** core/contracts/OffchainExchange.sol (L30-30)
```text
    mapping(bytes32 => int128) public filledAmounts;
```

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

**File:** core/contracts/Endpoint.sol (L271-294)
```text
    function submitTransactionsChecked(
        uint64 idx,
        bytes[] calldata transactions,
        bytes32 e,
        bytes32 s,
        uint8 signerBitmask
    ) external {
        validateSubmissionIdx(idx);
        require(msg.sender == sequencer);
        // TODO: if one of these transactions fails this means the sequencer is in an error state
        // we should probably record this, and engage some sort of recovery mode

        bytes32 digest = keccak256(abi.encode(idx));
        for (uint256 i = 0; i < transactions.length; ++i) {
            digest = keccak256(abi.encodePacked(digest, transactions[i]));
        }
        verifier.requireValidSignature(digest, e, s, signerBitmask);

        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            nSubmissions += 1;
        }
    }
```
