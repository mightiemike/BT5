### Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Unauthorized Depositors to Bypass the Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
The `DepositAllowlistExtension` is documented as gating `addLiquidity` by **depositor address**. However, its `beforeAddLiquidity` hook validates the `owner` parameter (the position beneficiary) rather than the `sender` parameter (the actual caller who pays tokens). Because `addLiquidity` explicitly supports an operator pattern where `msg.sender ≠ owner`, any unprivileged address can bypass the allowlist by naming an allowlisted address as `owner`.

### Finding Description
`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook: [1](#0-0) 

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  sender      owner (position beneficiary, caller-supplied)
```

`ExtensionCalling._beforeAddLiquidity` forwards both to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` then silently discards `sender` (first arg, unnamed) and gates only on `owner`: [3](#0-2) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

`msg.sender` here is the pool (correct), but the identity checked against the allowlist is `owner`, not the actual paying caller. The admin-facing setter and view function both use the word "depositor": [4](#0-3) 

confirming the intent is to restrict the paying party, not the position beneficiary.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swapper): [5](#0-4) 

The asymmetry between the two allowlist extensions confirms the deposit check is a bug, not a design choice.

### Impact Explanation
Any address not on the allowlist can call `pool.addLiquidity(allowlistedAddress, salt, deltas, ...)`, pay tokens via the swap callback, and have the position credited to the allowlisted address. The pool admin's access-control boundary is fully bypassed: the pool receives liquidity from an unauthorized source regardless of the allowlist configuration. This is an admin-boundary break — an unprivileged path circumvents a pool-admin-configured guard.

### Likelihood Explanation
The operator pattern (`msg.sender ≠ owner`) is a documented, first-class feature of `addLiquidity`. Any caller who knows an allowlisted address (e.g., from on-chain events) can exploit this immediately with no special privileges, no malicious token, and no oracle manipulation. The only cost to the attacker is the tokens they deposit (which are credited to the allowlisted address and can be recovered via collusion or social engineering).

### Recommendation
Change `beforeAddLiquidity` to validate `sender` (the actual paying caller) instead of `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to restrict position ownership (not the paying party), rename the extension and its setter/getter to `OwnerAllowlist` to avoid future confusion, and document the operator-pattern interaction explicitly.

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only `addressA`.
2. `addressB` (not allowlisted) calls:
   ```solidity
   pool.addLiquidity(
       addressA,          // owner — allowlisted, passes the check
       salt,
       deltas,
       callbackData,      // B pays tokens here
       extensionData
   );
   ```
3. The pool calls `extension.beforeAddLiquidity(addressB, addressA, ...)`.
4. The extension evaluates `allowedDepositor[pool][addressA]` → `true` → no revert.
5. `addressB` pays tokens via the callback; the position is recorded under `(addressA, salt)`.
6. The deposit allowlist is bypassed: `addressB` has injected liquidity into a restricted pool.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-30)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```
