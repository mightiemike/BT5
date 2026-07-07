### Title
Insufficient 3-Byte Subaccount Type Discriminator Allows Regular Subaccounts to Masquerade as Isolated — (File: `core/contracts/libraries/RiskHelper.sol`)

---

### Summary

`RiskHelper.isIsolatedSubaccount()` identifies isolated subaccounts by checking only the last **3 bytes** of a `bytes32` subaccount value against the ASCII magic `"iso"` (`0x69736F`). Because a regular subaccount is formed as `address (20 bytes) || name (12 bytes)`, any user who registers a subaccount name whose last 3 bytes equal `0x69736F` will have their regular subaccount permanently misidentified as an isolated subaccount by every protocol component that calls this function.

---

### Finding Description

The isolated subaccount layout is documented in `OffchainExchange.sol`:

```
// |  address | reserved | productId |   id   |  'iso'  |
// | 20 bytes |  6 bytes |  2 bytes  | 1 byte | 3 bytes |
```

The discriminator function in `RiskHelper.sol` is:

```solidity
function isIsolatedSubaccount(bytes32 subaccount)
    internal pure returns (bool)
{
    return uint256(subaccount) & 0xFFFFFF == 6910831; // 0x69736F = "iso"
}
```

Only the lowest 24 bits (3 bytes) are tested. A regular subaccount is constructed in `Endpoint.sol` as `bytes32(abi.encodePacked(msg.sender, subaccountName))` where `subaccountName` is a user-supplied `bytes12`. If the user supplies a name whose last 3 bytes are `0x69736F` (e.g., `"myaccountiso"`), the resulting `bytes32` passes the `isIsolatedSubaccount` check.

Because `isIsolatedSubaccount` is the sole gate used by every downstream component — `Clearinghouse.sol` (16 call-sites), `ClearinghouseLiq.sol` (4 call-sites), `EndpointTx.sol` (8 call-sites), and `OffchainExchange.sol` (10 call-sites) — the misidentification propagates everywhere.

Additionally, the two derived helpers also operate on the crafted subaccount:

```solidity
function getIsolatedProductId(bytes32 subaccount) internal pure returns (uint32) {
    if (!isIsolatedSubaccount(subaccount)) { return 0; }
    return uint32((uint256(subaccount) >> 32) & 0xFFFF); // bits from user-controlled name
}

function getIsolatedId(bytes32 subaccount) internal pure returns (uint8) {
    if (!isIsolatedSubaccount(subaccount)) { return 0; }
    return uint8((uint256(subaccount) >> 24) & 0xFF);    // bits from user-controlled name
}
```

For a crafted regular subaccount, `getIsolatedProductId` extracts bits 32–47 of the `bytes32`, which fall inside the user-controlled `subaccountName` field. The attacker therefore controls the "product ID" that the protocol believes this isolated subaccount is scoped to.

---

### Impact Explanation

The misidentification produces two classes of harm:

**1. Health / liquidation accounting corruption (solvency impact)**
`ClearinghouseLiq.sol` and `Clearinghouse.sol` use `isIsolatedSubaccount` and `getIsolatedProductId` to determine which product's risk parameters govern a subaccount's health. A regular subaccount that is misidentified as isolated will have its health evaluated under the isolated-subaccount path, scoped to the attacker-chosen product ID extracted from the name bytes. Cross-margin positions in other products may be excluded from the health calculation, allowing the subaccount to carry under-collateralised positions that would otherwise trigger liquidation. This is a direct solvency / accounting corruption impact.

**2. Operation gating bypass / denial**
`EndpointTx.sol` line 308 enforces `require(!RiskHelper.isIsolatedSubaccount(txn.recipient))` for `NlpProfitShare`, and `OffchainExchange.sol` line 1004 enforces `require(!RiskHelper.isIsolatedSubaccount(txn.order.sender))` for `createIsolatedSubaccount`. A crafted regular subaccount is permanently blocked from these operations, and conversely, isolated-only code paths are applied to it.

---

### Likelihood Explanation

The trigger requires only that a user register a subaccount name ending in the 3-byte sequence `0x69736F`. This is entirely within the user's control at deposit time via `Endpoint.depositCollateral(bytes12 subaccountName, ...)`. No privileged access, sequencer compromise, or social engineering is required. The attacker needs only to call the public `depositCollateral` entry point with a crafted name.

---

### Recommendation

Extend the isolated subaccount discriminator to cover the full reserved + productId + id + marker region (at minimum 6 bytes, ideally the full lower 12 bytes that distinguish isolated from regular subaccounts). Validate that the upper 20 bytes match the caller's address and that the reserved 6 bytes are zero, so that no user-supplied `bytes12` name can satisfy the check:

```solidity
function isIsolatedSubaccount(bytes32 subaccount) internal pure returns (bool) {
    // Check full lower 12 bytes: reserved(6) must be 0, marker(3) must be "iso"
    // This prevents any regular subaccount name from colliding.
    return (uint256(subaccount) & 0xFFFFFFFFFFFFFFFFFFFFFFFF) >> 24 == 0
        && uint256(subaccount) & 0xFFFFFF == 6910831;
}
```

Alternatively, use a longer, non-ASCII, protocol-internal marker that cannot be typed as a printable subaccount name.

---

### Proof of Concept

1. Alice calls `Endpoint.depositCollateral("myaccountiso", productId, amount)`.
   - The last 3 bytes of `"myaccountiso"` are `0x69 0x73 0x6F` = `"iso"` = `6910831`.
   - The resulting subaccount `bytes32` is `bytes32(abi.encodePacked(alice_address, bytes12("myaccountiso")))`.

2. `RiskHelper.isIsolatedSubaccount(aliceSubaccount)` returns `true`.

3. `RiskHelper.getIsolatedProductId(aliceSubaccount)` returns `uint32((uint256(aliceSubaccount) >> 32) & 0xFFFF)`, which equals the 2-byte slice of `"myaccountiso"` at offset 7–8 — fully attacker-controlled.

4. Every call-site in `Clearinghouse.sol`, `ClearinghouseLiq.sol`, `EndpointTx.sol`, and `OffchainExchange.sol` that branches on `isIsolatedSubaccount` now applies isolated-subaccount logic to Alice's regular cross-margin subaccount, scoping health checks to the attacker-chosen product ID and bypassing cross-margin collateral requirements. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** core/contracts/libraries/RiskHelper.sol (L83-89)
```text
    function isIsolatedSubaccount(bytes32 subaccount)
        internal
        pure
        returns (bool)
    {
        return uint256(subaccount) & 0xFFFFFF == 6910831;
    }
```

**File:** core/contracts/libraries/RiskHelper.sol (L91-107)
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
    }

    function getIsolatedId(bytes32 subaccount) internal pure returns (uint8) {
        if (!isIsolatedSubaccount(subaccount)) {
            return 0;
        }
        return uint8((uint256(subaccount) >> 24) & 0xFF);
    }
```

**File:** core/contracts/OffchainExchange.sol (L1003-1007)
```text
        require(
            !RiskHelper.isIsolatedSubaccount(txn.order.sender),
            ERR_UNAUTHORIZED
        );
        require(_isIsolated(txn.order.appendix), ERR_UNAUTHORIZED);
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

**File:** core/contracts/EndpointTx.sol (L302-309)
```text
            require(
                address(uint160(bytes20(txn.recipient))) ==
                    nlpPools[txn.poolId].owner,
                ERR_UNAUTHORIZED
            );
            requireSubaccount(txn.recipient);
            require(!RiskHelper.isIsolatedSubaccount(txn.recipient));
            clearinghouse.nlpProfitShare(
```
