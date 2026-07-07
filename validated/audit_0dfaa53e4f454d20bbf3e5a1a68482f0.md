### Title
`isDirectDepositV1Ready` and `isWrapVaultAssetReady` Omit Sanctions Check Present in Actual Deposit Execution — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.sol` exposes two deposit-readiness simulation functions — `isDirectDepositV1Ready` and `isWrapVaultAssetReady` — that are used by off-chain systems to determine whether to trigger a deposit. These functions evaluate only the minimum deposit amount threshold, but omit the sanctions check that the actual deposit execution path enforces. As a result, both functions return `true` for sanctioned addresses, while the actual deposit will revert.

---

### Finding Description

`isDirectDepositV1Ready` (lines 564–585) and `isWrapVaultAssetReady` (lines 536–562) in `ContractOwner.sol` simulate deposit readiness by calling `_isDepositAmountReady`, which checks only oracle price validity and minimum deposit amount: [1](#0-0) 

Neither function checks whether the recipient address is sanctioned.

The actual deposit execution path is `depositCollateralWithReferral` in `Endpoint.sol`, which enforces: [2](#0-1) 

`requireUnsanctioned` is defined in `EndpointStorage.sol` and reverts if the address is on the OFAC sanctions list: [3](#0-2) 

When `creditDepositV1` is called, it invokes `DirectDepositV1.creditDeposit()`, which ultimately calls `endpoint.depositCollateralWithReferral(subaccount, ...)`. At that point, `requireUnsanctioned(sender)` is called with `sender = address(bytes20(subaccount))` — the subaccount owner / recipient. If that address is sanctioned, the call reverts.

The simulation functions never replicate this check: [4](#0-3) 

---

### Impact Explanation

Off-chain automation systems (keepers, bots, UI backends) that call `isDirectDepositV1Ready` or `isWrapVaultAssetReady` to decide whether to invoke `creditDepositV1` or `wrapVaultAsset` will receive a `true` signal for sanctioned recipients. The subsequent on-chain execution will revert at the `requireUnsanctioned` check. This causes:

- Repeated failed transactions and wasted gas for automated systems.
- Inaccurate readiness signals surfaced to users or integrators, analogous to the BunniQuoter issue where quote functions omit hooklet checks that affect actual execution.

**Impact: Medium** — No direct asset loss, but the simulation contract's stated purpose (accurately predicting whether a deposit will succeed) is broken for a protocol-enforced constraint (sanctions), leading to operational failures and misleading state for any caller relying on these functions.

---

### Likelihood Explanation

The Nado protocol explicitly integrates a sanctions list (`ISanctionsList`) and enforces it on every deposit path. Any sanctioned address that has funds sitting in a `DirectDepositV1` contract will cause `isDirectDepositV1Ready` to return `true` indefinitely, while every actual execution attempt fails. This is a realistic scenario given the protocol's compliance posture.

**Likelihood: Medium**

---

### Recommendation

Add a sanctions check inside `isDirectDepositV1Ready` and `isWrapVaultAssetReady` (or inside `_isDepositAmountReady`) that mirrors the check in `depositCollateralWithReferral`:

```solidity
function _isDepositAmountReady(
    uint32 productId,
    uint256 balance,
    bool isFirstDeposit,
    address recipient          // add recipient parameter
) internal returns (bool) {
    // Mirror the sanctions check from depositCollateralWithReferral
    if (sanctions.isSanctioned(recipient)) {
        return false;
    }
    int128 oraclePriceX18 = spotEngine.getRisk(productId).priceX18;
    ...
}
```

Similarly, the isolated-subaccount guard (`!RiskHelper.isIsolatedSubaccount(subaccount)`) present in `depositCollateralWithReferral` should also be reflected in the readiness functions if the subaccount identity is available.

---

### Proof of Concept

1. Address `0xSanctioned` is added to the OFAC sanctions list tracked by `ISanctionsList`.
2. `0xSanctioned` sends tokens to its `DirectDepositV1` contract.
3. An off-chain keeper calls `ContractOwner.isDirectDepositV1Ready(0xSanctioned, false)`.
4. The function evaluates only the balance and minimum deposit threshold — both pass — and returns `true`.
5. The keeper calls `ContractOwner.creditDepositV1(subaccount)` where `subaccount` encodes `0xSanctioned`.
6. `DirectDepositV1.creditDeposit()` calls `endpoint.depositCollateralWithReferral(subaccount, ...)`.
7. `requireUnsanctioned(sender)` reverts with `ERR_WALLET_SANCTIONED`.
8. The keeper loops indefinitely, wasting gas, while `isDirectDepositV1Ready` continues to return `true`. [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/ContractOwner.sol (L502-508)
```text
    function creditDepositV1(bytes32 subaccount) external {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        if (directDepositV1 == address(0)) {
            directDepositV1 = createDirectDepositV1(subaccount);
        }
        DirectDepositV1(directDepositV1).creditDeposit();
    }
```

**File:** core/contracts/ContractOwner.sol (L564-585)
```text
    function isDirectDepositV1Ready(address recipient, bool isFirstDeposit)
        external
        returns (bool)
    {
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0));

            IERC20Base token = IERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(recipient);
            if (tokenAddr == wrappedNative) {
                balance += recipient.balance;
            }
            balance *= 10**(18 - token.decimals());
            if (_isDepositAmountReady(productId, balance, isFirstDeposit)) {
                return true;
            }
        }
        return false;
    }
```

**File:** core/contracts/ContractOwner.sol (L587-601)
```text
    function _isDepositAmountReady(
        uint32 productId,
        uint256 balance,
        bool isFirstDeposit
    ) internal returns (bool) {
        int128 oraclePriceX18 = spotEngine.getRisk(productId).priceX18;
        if (oraclePriceX18 <= 0) {
            return false;
        }
        if (balance > INT128_MAX) {
            return true;
        }
        return
            oraclePriceX18.mul(int128(uint128(balance))) >=
            (isFirstDeposit ? MIN_FIRST_DEPOSIT_AMOUNT : MIN_DEPOSIT_AMOUNT);
```

**File:** core/contracts/Endpoint.sol (L123-135)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);
```

**File:** core/contracts/EndpointStorage.sol (L121-123)
```text
    function requireUnsanctioned(address sender) internal view virtual {
        require(!sanctions.isSanctioned(sender), ERR_WALLET_SANCTIONED);
    }
```
