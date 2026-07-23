The vulnerability is real and confirmed. Here is the full analysis:

---

### Title
`DepositAllowlistExtension` gates `owner` instead of `sender`, allowing any attacker to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual token payer / operator) and only checks `owner` (the LP-share recipient). Because `MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` with no restriction, any attacker can pass an allowlisted address as `owner`, satisfy the extension check, and inject liquidity into a curated pool while paying with their own tokens.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to `_beforeAddLiquidity`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first parameter but leaves it **unnamed and unused**. The allowlist check is performed exclusively on `owner`: [3](#0-2) 

Because `pool.addLiquidity` imposes no constraint on who may supply any `owner` value, an attacker calls:

```
pool.addLiquidity(owner = allowedUser, ...)
```

The extension evaluates `allowedDepositor[pool][allowedUser]` → `true`, the hook passes, the attacker's tokens are pulled via the `metricOmmModifyLiquidityCallback`, and LP shares are minted under `allowedUser`'s position key. The attacker's identity (`sender`) is never consulted.

---

### Impact Explanation

- **Pool curation is broken.** The entire purpose of `DepositAllowlistExtension` is to restrict which operators can fund a curated pool. Any unprivileged address can bypass this gate by nominating an allowlisted address as `owner`.
- **Attacker can manipulate pool state.** By injecting liquidity into specific bins, the attacker shifts bin balances and the effective price curve, affecting subsequent swaps for all users of the pool.
- **Attacker cannot directly recover tokens** (since `removeLiquidity` enforces `msg.sender == owner`), but the pool state manipulation and curation bypass are the primary harms. [4](#0-3) 

---

### Likelihood Explanation

The attack requires only:
1. Knowledge of one allowlisted address for the target pool (readable from `allowedDepositor` public mapping).
2. A direct call to `pool.addLiquidity` with `owner = allowedUser`.

No privileged role, no special token, no off-chain data. Likelihood is **High**.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual payer/operator) instead of `owner`:

```solidity
// current (broken)
function beforeAddLiquidity(address, address owner, ...)
    ...
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// fixed
function beforeAddLiquidity(address sender, address, ...)
    ...
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
```

If the intent is to gate both the payer and the LP-share recipient, both should be checked.

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_depositAllowlist_bypass_via_owner_param() public {
    // Setup: only allowedUser is on the allowlist
    depositExtension.setAllowedToDeposit(address(pool), allowedUser, true);

    // Attacker (not on allowlist) calls pool directly with owner = allowedUser
    vm.startPrank(attacker);
    token0.approve(address(pool), type(uint256).max);
    token1.approve(address(pool), type(uint256).max);

    // This should revert NotAllowedToDeposit but PASSES because
    // the extension checks owner (allowedUser) not sender (attacker)
    pool.addLiquidity(allowedUser, salt, deltas, callbackData, "");
    vm.stopPrank();

    // Attacker's tokens were pulled; LP shares credited to allowedUser
    // Confirm attacker's own add (owner=attacker) correctly reverts:
    vm.startPrank(attacker);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    pool.addLiquidity(attacker, salt, deltas, callbackData, "");
    vm.stopPrank();
}
``` [3](#0-2)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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
