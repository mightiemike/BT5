### Title
Incorrect `verifyingContract` in `getDigest()` Domain Separator Breaks EIP-712 Binding and Enables Cross-Contract Order Replay — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.getDigest()` manually constructs an EIP-712 domain separator using `address(uint160(productId))` — a `uint32` product ID cast to an address — as the `verifyingContract` field, instead of `address(this)`. This is the direct analog to the reported `BLUEPRINT_TYPE_HASH` misuse: a wrong constant/value is placed in the domain separator, breaking EIP-712 compliance and eliminating contract-binding protection.

---

### Finding Description

In `OffchainExchange.sol`, the `getDigest()` function constructs the domain separator as follows:

```solidity
bytes32 domainSeparator = keccak256(
    abi.encode(
        _TYPE_HASH,
        _EIP712NameHash(),
        _EIP712VersionHash(),
        block.chainid,
        address(uint160(productId))   // ← wrong: should be address(this)
    )
);
``` [1](#0-0) 

The `_TYPE_HASH` is correctly defined as the standard EIP-712 domain type hash:

```solidity
bytes32 private constant _TYPE_HASH =
    keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );
``` [2](#0-1) 

The `verifyingContract` field in EIP-712 is defined by the standard as "the Ethereum address of the contract that will verify the signature." Here it is set to `address(uint160(productId))` — for example, product ID `1` maps to `0x0000000000000000000000000000000000000001`, which is not a real contract. This means the domain separator does not bind to the actual `OffchainExchange` contract address.

By contrast, the `Endpoint`/`EndpointTx` transaction path correctly uses the inherited `_hashTypedDataV4()` from `EIP712Upgradeable`, which internally uses `address(this)`: [3](#0-2) 

The `getDigest()` result is used directly in `matchOrders` to compute `ordersInfo.taker.digest` and `ordersInfo.maker.digest`, which are then passed to `_validateOrder` → `_checkSignature` for ECDSA recovery: [4](#0-3) [5](#0-4) 

---

### Impact Explanation

**1. Signature failures for standard EIP-712 signers.**
Any off-chain client (wallet, SDK, integration) that independently implements EIP-712 using `address(this)` (the actual `OffchainExchange` contract) as `verifyingContract` — as the standard requires — will compute a different digest than the contract. Their signatures will fail `_checkSignature`, causing all such orders to be rejected as `ERR_INVALID_TAKER` or `ERR_INVALID_MAKER`.

**2. Cross-contract order replay on contract upgrade.**
Because the domain separator does not include the real contract address, an order signature valid on the current `OffchainExchange` deployment is equally valid on any future redeployment or upgraded contract that uses the same `productId`, `name`, `version`, and `chainId`. The `filledAmounts` mapping is per-contract storage, so a newly deployed contract has no record of prior fills. An attacker can replay previously filled (or cancelled) orders on the new contract, causing unauthorized trades and balance mutations.

---

### Likelihood Explanation

Medium. The `getDigest()` function is public and is the canonical on-chain digest source. Clients that call `getDigest()` and sign the returned bytes will produce matching signatures. However:
- Any third-party integration or wallet using standard EIP-712 typed-data signing (MetaMask `eth_signTypedData_v4`, ethers.js `_signTypedData`) with `address(this)` will fail.
- The cross-contract replay vector is triggered on any contract upgrade or redeployment, which is a realistic operational event.

---

### Recommendation

Replace `address(uint160(productId))` with `address(this)` in the domain separator. To preserve per-product domain isolation (preventing cross-product order replay), include `productId` as an explicit field in the `Order` struct type string instead:

```diff
- string memory structType = "Order(bytes32 sender,int128 priceX18,int128 amount,uint64 expiration,uint64 nonce,uint128 appendix)";
+ string memory structType = "Order(bytes32 sender,int128 priceX18,int128 amount,uint64 expiration,uint64 nonce,uint128 appendix,uint32 productId)";

  bytes32 structHash = keccak256(
      abi.encode(
          keccak256(bytes(structType)),
          order.sender,
          order.priceX18,
          order.amount,
          order.expiration,
          order.nonce,
-         order.appendix
+         order.appendix,
+         productId
      )
  );

  bytes32 domainSeparator = keccak256(
      abi.encode(
          _TYPE_HASH,
          _EIP712NameHash(),
          _EIP712VersionHash(),
          block.chainid,
-         address(uint160(productId))
+         address(this)
      )
  );
```

---

### Proof of Concept

1. Deploy `OffchainExchange` at address `0xABCD...`.
2. A trader signs an order for `productId = 1` using standard EIP-712 tooling with `verifyingContract = 0xABCD...`.
3. On-chain, `getDigest(1, order)` computes the domain separator with `verifyingContract = address(uint160(1)) = 0x0000...0001`.
4. The digests differ → `ECDSA.recover` returns a wrong address → `_checkSignature` returns `false` → order rejected.

For the replay scenario:
1. Trader signs order for `productId = 1` using `getDigest()` output (matching the on-chain logic).
2. Order is filled; `filledAmounts[digest]` is set on the old contract.
3. `OffchainExchange` is upgraded/redeployed at a new address.
4. The new contract computes the identical `domainSeparator` for `productId = 1` (since `verifyingContract = address(uint160(1))` is independent of the contract address).
5. The same signature is valid on the new contract; `filledAmounts[digest]` is zero → order can be matched again. [6](#0-5)

### Citations

**File:** core/contracts/OffchainExchange.sol (L99-102)
```text
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

**File:** core/contracts/OffchainExchange.sol (L660-672)
```text
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
