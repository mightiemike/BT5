### Title
`txn.productId` Exceeding 16-Bit Field Width Corrupts Isolated Subaccount Encoding and Enables Cross-Product Subaccount Confusion — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`createIsolatedSubaccount` in `OffchainExchange.sol` packs `txn.productId` (a `uint32`, up to ~4.29 billion) into a 2-byte (16-bit) field within the isolated subaccount `bytes32` encoding, with no upper-bound check. When `txn.productId > 0xFFFF`, the upper bits overflow into the "reserved" 6-byte field, corrupting the encoding. The read-back function `getIsolatedProductId` masks to 16 bits, returning a truncated productId. This causes the deduplication logic to confuse isolated subaccounts across two distinct productIds that share the same lower 16 bits, allowing a user to hijack another user's (or their own) isolated subaccount slot for a different product.

---

### Finding Description

The isolated subaccount `bytes32` identifier is constructed with the following layout:

```
// |  address | reserved | productId |   id   |  'iso'  |
// | 20 bytes |  6 bytes |  2 bytes  | 1 byte | 3 bytes |
```

The packing at `OffchainExchange.sol` line 1059 is:

```solidity
(uint256(txn.productId) << 32)
```

`txn.productId` is a `uint32`. The layout allocates only 2 bytes (bits 32–47) for productId. If `txn.productId > 0xFFFF`, bits 48 and above are set, which falls into the "reserved" 6-byte field (bits 48–95). No bounds check exists anywhere in the call path.

The read-back in `RiskHelper.getIsolatedProductId` at line 99 is:

```solidity
return uint32((uint256(subaccount) >> 32) & 0xFFFF);
```

This masks to 16 bits, returning `txn.productId & 0xFFFF` — a truncated, wrong value.

The deduplication loop in `createIsolatedSubaccount` (lines 1025–1038) iterates over existing isolated subaccounts and checks:

```solidity
uint32 productId = RiskHelper.getIsolatedProductId(subaccount);
if (productId == txn.productId) { ... }
```

Because `getIsolatedProductId` returns the truncated value, two productIds sharing the same lower 16 bits (e.g., `0x10001` and `0x0001`) are treated as identical by this check.

---

### Impact Explanation

**Concrete attack scenario — cross-product subaccount hijack:**

1. Attacker submits `CreateIsolatedSubaccount` with `productId = 0x10001` (a non-existent or attacker-chosen product). The subaccount is created; `getIsolatedProductId` returns `0x0001` for it.
2. A victim (or the attacker themselves) later submits `CreateIsolatedSubaccount` with the real `productId = 0x0001`.
3. The deduplication loop finds the existing subaccount (created for `0x10001`), sees `getIsolatedProductId` returns `0x0001 == txn.productId`, and returns the corrupted subaccount as the isolated subaccount for product `0x0001`.
4. `digestToSubaccount[digest]` for the victim's order is set to the corrupted subaccount. Any margin transferred into this subaccount (`spotEngine.updateBalance`) goes into the corrupted identifier.
5. The corrupted subaccount has non-zero bits in the "reserved" field, meaning it is a unique `bytes32` key that does not correspond to any correctly-formed subaccount. Funds deposited there may be permanently inaccessible or misattributed.

**Secondary impact:** The corrupted subaccount's `bytes32` value is used as a key in `parentSubaccounts` and `isolatedSubaccounts` mappings. Any downstream logic that reconstructs or validates the subaccount from its packed fields (e.g., address extraction, productId routing) will operate on a malformed identifier, leading to accounting desynchronization.

---

### Likelihood Explanation

The `CreateIsolatedSubaccount` transaction is a standard user-facing entry point reachable by any trader. `txn.productId` is a user-supplied `uint32` field with no on-chain validation in `EndpointTx.sol` or `OffchainExchange.sol`. An attacker needs only to submit a well-formed signed order with `productId > 0xFFFF`. No privileged access is required.

---

### Recommendation

Add a bounds check in `createIsolatedSubaccount` before packing:

```solidity
require(txn.productId <= type(uint16).max, "productId exceeds 16-bit field");
```

This mirrors the exact fix recommended in the external report: enforce that the parameter fits within the bit-width allocated for it in the packed encoding.

---

### Proof of Concept

**Root cause — packing without bounds check:** [1](#0-0) 

**Read-back truncates to 16 bits, losing upper bits of productId:** [2](#0-1) 

**Deduplication check compares truncated value against full uint32, enabling cross-product confusion:** [3](#0-2) 

**No bounds check exists anywhere in the entry path:** [4](#0-3)

### Citations

**File:** core/contracts/OffchainExchange.sol (L999-1020)
```text
    function createIsolatedSubaccount(
        IEndpoint.CreateIsolatedSubaccount memory txn,
        address linkedSigner
    ) external onlyEndpoint returns (bytes32) {
        require(
            !RiskHelper.isIsolatedSubaccount(txn.order.sender),
            ERR_UNAUTHORIZED
        );
        require(_isIsolated(txn.order.appendix), ERR_UNAUTHORIZED);
        bytes32 digest = getDigest(txn.productId, txn.order);
        if (digestToSubaccount[digest] != bytes32(0)) {
            return digestToSubaccount[digest];
        }
        require(
            _checkSignature(
                txn.order.sender,
                digest,
                linkedSigner,
                txn.signature
            ),
            ERR_INVALID_SIGNATURE
        );
```

**File:** core/contracts/OffchainExchange.sol (L1029-1035)
```text
                    uint32 productId = RiskHelper.getIsolatedProductId(
                        subaccount
                    );
                    if (productId == txn.productId) {
                        newIsolatedSubaccount = subaccount;
                        break;
                    }
```

**File:** core/contracts/OffchainExchange.sol (L1055-1062)
```text
            // |  address | reserved | productId |   id   |  'iso'  |
            // | 20 bytes |  6 bytes |  2 bytes  | 1 byte | 3 bytes |
            newIsolatedSubaccount = bytes32(
                (uint256(uint160(senderAddress)) << 96) |
                    (uint256(txn.productId) << 32) |
                    (uint256(id) << 24) |
                    6910831
            );
```

**File:** core/contracts/libraries/RiskHelper.sol (L91-99)
```text
    function getIsolatedProductId(bytes32 subaccount)
        internal
        pure
        returns (uint32)
    {
        if (!isIsolatedSubaccount(subaccount)) {
            return 0;
        }
        return uint32((uint256(subaccount) >> 32) & 0xFFFF);
```
