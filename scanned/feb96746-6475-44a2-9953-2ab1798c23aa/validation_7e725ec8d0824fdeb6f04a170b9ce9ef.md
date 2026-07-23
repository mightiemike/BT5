The call chain is fully traceable and the bug is confirmed. Here is the complete analysis:

---

### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` ignores the `sender` argument (the actual caller/payer) and instead gates on `owner` (the position recipient). Any address not on the allowlist can call `pool.addLiquidity(allowlisted_owner, ...)` directly, pass the guard, have their own tokens pulled via callback, and credit LP shares to the allowlisted owner — completely defeating the restriction the extension is designed to enforce.

---

### Finding Description

The pool's `addLiquidity` entry point passes both the real caller and the position owner to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both and forwards them to the extension: [2](#0-1) 

The extension receives `(address sender, address owner, ...)` but silently discards `sender` (unnamed first parameter) and checks only `owner`: [3](#0-2) 

Inside the extension, `msg.sender` is the **pool** (the caller of the extension), so the effective check is:

```
allowedDepositor[pool][owner]   // owner = position recipient, NOT the depositor
```

The mapping is named `allowedDepositor` and the NatSpec says *"Gates `addLiquidity` by depositor address"*, confirming the intent was to check the actual depositor (sender), not the position recipient. [4](#0-3) 

---

### Impact Explanation

An unprivileged attacker who is **not** in `allowedDepositor` can:

1. Call `pool.addLiquidity(allowlisted_owner, salt, deltas, callbackData, extensionData)` directly.
2. The extension checks `allowedDepositor[pool][allowlisted_owner]` → **passes**.
3. The pool issues a callback to the attacker; the attacker's tokens are pulled.
4. LP shares are credited to `allowlisted_owner`.

The deposit allowlist is rendered completely ineffective: any actor can deposit into a restricted pool by nominating any allowlisted address as `owner`. The pool admin's access-control intent is bypassed by an unprivileged, permissionless call path. This is an admin-boundary break under the contest impact gate.

---

### Likelihood Explanation

The attack requires only a direct call to the public `pool.addLiquidity` function with a known allowlisted address as `owner`. No privileged access, no special setup, and no non-standard token behavior is needed. Any allowlisted address is publicly discoverable via `AllowedToDepositSet` events. Likelihood is **High**.

---

### Recommendation

Use `sender` (the actual depositor/caller) for the allowlist check, not `owner`:

```solidity
// Before (buggy):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (fixed):
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
``` [3](#0-2) 

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_allowlistBypass() public {
    address allowlistedOwner = makeAddr("allowlistedOwner");
    address attacker         = makeAddr("attacker");

    // Pool admin allowlists `allowlistedOwner`, NOT `attacker`
    vm.prank(poolAdmin);
    extension.setAllowedToDeposit(address(pool), allowlistedOwner, true);

    // Fund attacker and approve pool
    deal(token0, attacker, 1_000e18);
    deal(token1, attacker, 1_000e18);
    vm.startPrank(attacker);
    IERC20(token0).approve(address(pool), type(uint256).max);
    IERC20(token1).approve(address(pool), type(uint256).max);

    // Attacker calls addLiquidity with allowlistedOwner as `owner`
    // beforeAddLiquidity checks allowedDepositor[pool][allowlistedOwner] → passes
    pool.addLiquidity(allowlistedOwner, salt, deltas, callbackData, extensionData);
    vm.stopPrank();

    // Assert: attacker's tokens were pulled
    assertLt(IERC20(token0).balanceOf(attacker), 1_000e18);
    // Assert: LP shares credited to allowlistedOwner, not attacker
    assertGt(positionShares(allowlistedOwner, salt, bin), 0);
    assertEq(positionShares(attacker, salt, bin), 0);
}
```

The `beforeAddLiquidity` guard passes because it checks the allowlisted `owner`, not the non-allowlisted `attacker` who is the actual payer. The allowlist is fully bypassed.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
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
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-13)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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
