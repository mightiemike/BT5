The vulnerability is real and confirmed. Here is the complete trace:

**Call path:**
1. `MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` to `_beforeAddLiquidity` [1](#0-0) 

2. `ExtensionCalling._beforeAddLiquidity` encodes both `sender` and `owner` and forwards them to the extension [2](#0-1) 

3. `DepositAllowlistExtension.beforeAddLiquidity` receives `(sender, owner, ...)` but **explicitly drops `sender`** (unnamed first parameter) and checks only `allowedDepositor[msg.sender][owner]` [3](#0-2) 

The `IMetricOmmExtensions` interface clearly names the first parameter `sender` — it is available and intentionally passed — but the extension ignores it entirely. [4](#0-3) 

---

### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any non-allowlisted caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension` is designed to gate `addLiquidity` to a per-pool allowlist of depositors. However, its `beforeAddLiquidity` hook ignores the `sender` argument (the actual `msg.sender` of the pool call) and instead validates only `owner` (the position recipient). Because `owner` is a free caller-supplied argument with no `msg.sender == owner` constraint on `addLiquidity`, any non-allowlisted address can pass the check by supplying an allowlisted address as `owner`.

### Finding Description
`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address and imposes no requirement that `msg.sender == owner`. It passes `msg.sender` as `sender` and the caller-supplied value as `owner` to every configured extension hook. `DepositAllowlistExtension.beforeAddLiquidity` silently discards `sender` (the parameter is unnamed) and evaluates `allowedDepositor[pool][owner]`. An attacker who knows any allowlisted address `A` can call `pool.addLiquidity(owner=A, ...)` from a non-allowlisted address `B`; the extension sees `allowedDepositor[pool][A] == true` and permits the call. The LP shares are minted to `A`, and `B` pays the tokens via the modify-liquidity callback.

Note: `removeLiquidity` enforces `msg.sender == owner`, so the attacker cannot recover the deposited tokens through that path. The griefing vector is one-directional: B loses tokens, A receives an unsolicited LP position.

### Impact Explanation
The deposit allowlist — the sole access-control mechanism for restricted pools — is fully bypassed by any caller who knows a single allowlisted address. The pool admin's intent (only allowlisted addresses may deposit) is violated. Non-allowlisted actors can add liquidity to restricted pools, and allowlisted users can be forced into LP positions they did not initiate. This breaks the core invariant of the extension and constitutes broken core pool functionality for any pool relying on `DepositAllowlistExtension` for access control.

### Likelihood Explanation
The attack requires only knowledge of one allowlisted address (publicly readable from `allowedDepositor` mapping or on-chain events) and the ability to call `pool.addLiquidity` directly (a public, permissionless entrypoint). No privileged access, oracle manipulation, or special token behavior is needed. Likelihood is high given the trivial preconditions.

### Recommendation
Check `sender` (the actual caller) instead of `owner` in `beforeAddLiquidity`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to allow third-party deposits on behalf of an allowlisted `owner` (as the periphery's `addLiquidityExactShares(pool, owner, ...)` supports), then both `sender` and `owner` should be checked: allow the call if either is allowlisted.

### Proof of Concept
```solidity
// Foundry integration test
function test_allowlistBypass() public {
    address allowlisted = makeAddr("allowlisted");
    address attacker    = makeAddr("attacker");

    // Admin allowlists only `allowlisted`
    vm.prank(admin);
    depositExtension.setAllowedToDeposit(address(pool), allowlisted, true);

    // Fund and approve attacker
    token0.mint(attacker, 1_000_000);
    token1.mint(attacker, 1_000_000);
    vm.startPrank(attacker);
    token0.approve(address(pool), type(uint256).max);
    token1.approve(address(pool), type(uint256).max);

    // Attacker calls addLiquidity with owner = allowlisted
    // Extension checks allowedDepositor[pool][allowlisted] == true → passes
    // Call succeeds; shares minted to `allowlisted`, tokens pulled from `attacker`
    pool.addLiquidity(allowlisted, 0, delta, callbackData, "");
    // Assert: call did NOT revert with NotAllowedToDeposit
    vm.stopPrank();
}
```

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
```
