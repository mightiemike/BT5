### Title
Unguarded `endpoint.depositCollateralWithReferral()` Call in `DirectDepositV1.creditDeposit()` Permanently Blocks Token Crediting When Subaccount Owner Is Sanctioned — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` forwards token balances held by the DDA contract to the Endpoint via `endpoint.depositCollateralWithReferral()` with no try/catch guard. `Endpoint.depositCollateralWithReferral()` performs a hard `require`-based sanctions check on the subaccount owner address. If that address is ever added to the OFAC sanctions list, every future call to `creditDeposit()` reverts unconditionally, permanently freezing any tokens already sitting in the DDA.

---

### Finding Description

`DirectDepositV1.creditDeposit()` iterates over all product IDs and, for each non-zero token balance, approves the endpoint and calls `endpoint.depositCollateralWithReferral()`:

```solidity
// DirectDepositV1.sol lines 83–101
function creditDeposit() external {
    uint32[] memory productIds = spotEngine.getProductIds();
    for (uint256 i = 0; i < productIds.length; i++) {
        ...
        token.approve(address(endpoint), balance);
        endpoint.depositCollateralWithReferral(   // ← no try/catch
            subaccount,
            productId,
            uint128(balance),
            "-1"
        );
    }
}
``` [1](#0-0) 

Inside `Endpoint.depositCollateralWithReferral()`, two hard-revert sanctions checks are performed before any token movement:

```solidity
// Endpoint.sol lines 131–135
address sender = address(bytes20(subaccount));
requireUnsanctioned(msg.sender);   // checks DDA contract address
requireUnsanctioned(sender);       // checks subaccount owner address
``` [2](#0-1) 

`requireUnsanctioned` is a hard revert:

```solidity
// EndpointStorage.sol lines 121–123
function requireUnsanctioned(address sender) internal view virtual {
    require(!sanctions.isSanctioned(sender), ERR_WALLET_SANCTIONED);
}
``` [3](#0-2) 

The `subaccount` field in `DirectDepositV1` is immutable — set once at construction and never changed:

```solidity
// DirectDepositV1.sol lines 32, 50
bytes32 internal subaccount;
...
subaccount = _subaccount;
``` [4](#0-3) 

Once the address embedded in `subaccount` is sanctioned, `requireUnsanctioned(sender)` will revert on every call. Because `creditDeposit()` has no try/catch, the entire transaction reverts and tokens already held in the DDA cannot be credited.

Notably, the Endpoint developers themselves recognized this exact class of problem for the `isValidDepositAmount` path and explicitly chose not to revert there:

```solidity
// Endpoint.sol lines 137–142
if (!isValidDepositAmount(subaccount, productId, amount)) {
    // we cannot revert here, otherwise direct deposit could be blocked when there are
    // multiple assets awaiting credit but one of them is below the minimum deposit amount.
    return;
}
``` [5](#0-4) 

The same defensive reasoning was not applied to the sanctions check path.

---

### Impact Explanation

Tokens sent to a `DirectDepositV1` address before the subaccount owner is sanctioned are frozen in the DDA. `creditDeposit()` is the only mechanism to forward them to the protocol. The `withdraw()` escape hatch is `onlyOwner`:

```solidity
// DirectDepositV1.sol lines 103–106
function withdraw(IIERC20Base token) external onlyOwner {
    uint256 balance = token.balanceOf(address(this));
    safeTransfer(token, msg.sender, balance);
}
``` [6](#0-5) 

If the DDA was deployed by a protocol-controlled factory (not the end user), the user has no self-rescue path. Even if the owner is the user, the funds are inaccessible to the protocol until manually rescued — matching the "temporary freezing of funds" impact class of the reference report.

---

### Likelihood Explanation

OFAC sanctions additions are real, recurring events. A user who sends tokens to their DDA address and is subsequently sanctioned (or whose address is mistakenly added) triggers this condition with no further attacker action required. The condition is permanent until the sanctions oracle is updated or the owner calls `withdraw()`. The missing try/catch is the necessary vulnerable step — the Endpoint's own comment confirms the developers understood that hard reverts in this flow are dangerous, but the sanctions path was left unguarded.

---

### Recommendation

Wrap the `endpoint.depositCollateralWithReferral()` call in a try/catch block inside `creditDeposit()`. On failure, either skip the product and continue (consistent with the `isValidDepositAmount` pattern already used in `Endpoint.sol`) or emit a recoverable event:

```solidity
try endpoint.depositCollateralWithReferral(subaccount, productId, uint128(balance), "-1") {
    // credited successfully
} catch {
    token.approve(address(endpoint), 0); // reset approval
    emit DepositCreditFailed(productId, balance);
}
```

This mirrors the Stargate composability best practice cited in the reference report and is consistent with the defensive pattern the Endpoint developers already applied to the minimum-deposit check.

---

### Proof of Concept

1. Deploy `DirectDepositV1` with a `_subaccount` encoding address `A`.
2. Transfer 1000 USDC to the DDA contract.
3. Add address `A` to the sanctions oracle.
4. Call `creditDeposit()` — the call reverts with `ERR_WALLET_SANCTIONED` at `requireUnsanctioned(sender)` inside `Endpoint.depositCollateralWithReferral()`.
5. The 1000 USDC remains locked in the DDA. No user-accessible function can forward it to the protocol. Only the DDA owner can call `withdraw()` to recover the raw tokens.

### Citations

**File:** core/contracts/DirectDepositV1.sol (L32-51)
```text
    bytes32 internal subaccount;
    address payable internal wrappedNative;

    event NativeTokenTransferFailed(uint256 amount);
    event DirectDepositV1Created(
        uint8 indexed version,
        bytes32 indexed subaccount,
        address dda
    );

    constructor(
        address _endpoint,
        address _spotEngine,
        bytes32 _subaccount,
        address payable _wrappedNative
    ) {
        endpoint = IIEndpoint(_endpoint);
        spotEngine = IISpotEngine(_spotEngine);
        subaccount = _subaccount;
        wrappedNative = _wrappedNative;
```

**File:** core/contracts/DirectDepositV1.sol (L83-101)
```text
    function creditDeposit() external {
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0), "Invalid productId.");
            IIERC20Base token = IIERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
        }
    }
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```

**File:** core/contracts/Endpoint.sol (L131-135)
```text
        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);
```

**File:** core/contracts/Endpoint.sol (L137-142)
```text
        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }
```

**File:** core/contracts/EndpointStorage.sol (L121-123)
```text
    function requireUnsanctioned(address sender) internal view virtual {
        require(!sanctions.isSanctioned(sender), ERR_WALLET_SANCTIONED);
    }
```
