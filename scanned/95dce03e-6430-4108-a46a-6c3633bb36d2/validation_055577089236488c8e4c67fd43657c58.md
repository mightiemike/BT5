### Title
`DepositAllowlistExtension` Gates on `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently ignores the `sender` argument (the actual caller of `pool.addLiquidity`) and instead checks the `owner` argument (the position recipient). Because `owner` is a free caller-controlled parameter, any unprivileged user can bypass the deposit allowlist on a curated pool by setting `owner` to any already-allowlisted address, depositing their own tokens into that address's position.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls `_beforeAddLiquidity(msg.sender, owner, ...)`, forwarding both the actual caller (`sender = msg.sender`) and the position recipient (`owner`) to the extension hook. [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both values and dispatches them to every configured extension in order: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but declares it unnamed (discarded). It then checks `allowedDepositor[msg.sender][owner]` — where `msg.sender` is the pool and `owner` is the position recipient supplied by the caller: [3](#0-2) 

Because `owner` is a free parameter that any caller can set to any address, the allowlist check is trivially bypassed: a non-allowlisted user passes an allowlisted address as `owner`, the check passes, and the caller's tokens are deposited into the allowlisted address's position.

This is structurally opposite to `SwapAllowlistExtension`, which correctly checks `sender` (the actual swap initiator): [4](#0-3) 

The bypass is reachable through two supported public paths:

**Direct pool call:** Bob calls `pool.addLiquidity(alice, salt, deltas, ...)`. The extension sees `owner = alice` (allowlisted) and passes.

**Via `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, alice, ...)`:** The adder's `_validateOwner` only rejects `address(0)`, so Bob freely sets `owner = alice`. The adder calls `pool.addLiquidity(alice, ...)`, and the extension again sees `owner = alice` and passes. [5](#0-4) 

The `addLiquidityWeighted` probe path is equally affected: the probe call passes `owner` to the pool, the extension checks `allowedDepositor[pool][owner]`, and if `owner` is allowlisted the probe succeeds, after which the paying deposit also passes. [6](#0-5) 

---

### Impact Explanation

The deposit allowlist is the primary curation mechanism for pools that restrict who may provide liquidity. With this bug the allowlist is completely ineffective: any unprivileged user can deposit into a curated pool by naming any allowlisted address as `owner`. The depositor's tokens enter the pool and are credited to the allowlisted address's position without that address's consent. This breaks the core pool invariant that only approved depositors may add liquidity, constitutes a broken core pool functionality finding, and can be used to force unwanted LP exposure onto allowlisted addresses or to manipulate the pool's liquidity distribution in ways the admin explicitly intended to prevent.

---

### Likelihood Explanation

The bypass requires no special role, no flash loan, and no complex setup. Any user who can read the allowlist (public mapping) can identify an allowlisted address and immediately execute the bypass through a direct pool call or the standard periphery adder. Likelihood is high.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual caller of `addLiquidity`) instead of `owner`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
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

This mirrors the correct pattern already used in `SwapAllowlistExtension` and ensures the allowlist gates the economically active actor — the address that initiates and pays for the deposit — rather than the freely chosen position recipient.

---

### Proof of Concept

```
1. Deploy pool with DepositAllowlistExtension configured on beforeAddLiquidity.
2. Pool admin calls extension.setAllowedToDeposit(pool, alice, true).
   → allowedDepositor[pool][alice] = true
   → Bob is NOT allowlisted.

3. Bob calls pool.addLiquidity(alice, salt, deltas, callbackData, extensionData).
   → pool calls _beforeAddLiquidity(msg.sender=Bob, owner=alice, ...)
   → extension receives: sender=Bob (ignored), owner=alice
   → check: allowedDepositor[pool][alice] == true → PASSES

4. Bob's tokens are pulled in the callback and credited to alice's position.
5. Bob has successfully deposited into a pool that was supposed to block him.

Alternatively via MetricOmmPoolLiquidityAdder:
3b. Bob calls adder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, extData).
    → _validateOwner(alice) passes (alice != address(0))
    → adder calls pool.addLiquidity(alice, ...)
    → same bypass as above.
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L106-115)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
```
