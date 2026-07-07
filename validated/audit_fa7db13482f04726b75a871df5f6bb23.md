### Title
`DirectDepositV1` Stores Immutable `endpoint` and `spotEngine` References That Cannot Be Updated After Protocol Migration — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1` hardcodes the `endpoint` and `spotEngine` contract addresses at construction time with no setter functions and no upgradeability. If the Nado protocol migrates either contract to a new proxy address, `creditDeposit()` becomes permanently broken. Any ETH or WETH held in the contract after such a migration has no path into the protocol and is locked until the owner manually recovers it.

---

### Finding Description

The `DirectDepositV1` constructor assigns `endpoint` and `spotEngine` directly from constructor arguments into internal state variables:

```solidity
endpoint = IIEndpoint(_endpoint);       // line 48
spotEngine = IISpotEngine(_spotEngine); // line 49
```

These are plain `internal` storage variables. The contract inherits only from `Ownable` (not `OwnableUpgradeable`), making it non-upgradeable. There are no setter functions anywhere in the contract for `endpoint`, `spotEngine`, or `wrappedNative`.

The `creditDeposit()` function — callable by any external account — depends on both stale references:

```solidity
uint32[] memory productIds = spotEngine.getProductIds();   // line 84
...
token.approve(address(endpoint), balance);                  // line 92
endpoint.depositCollateralWithReferral(...);                // line 93-98
```

The `receive()` function immediately wraps incoming ETH to WETH via `wrappedNative`:

```solidity
(bool success, ) = wrappedNative.call{value: msg.value}(""); // line 65
```

This means ETH is converted to WETH and held in the contract before `creditDeposit()` is ever called. If `endpoint` is stale at the time `creditDeposit()` is invoked, the `depositCollateralWithReferral()` call reverts, and the WETH accumulates in the contract with no user-accessible recovery path.

The Nado protocol uses `ProxyManager` to manage contract upgrades. A major version upgrade that deploys a new `Endpoint` proxy at a new address is a realistic and documented protocol operation (`registerRegularProxy`). `DirectDepositV1` is not registered with or managed by `ProxyManager` and has no upgrade path of its own.

---

### Impact Explanation

After a protocol migration to a new `Endpoint` or `SpotEngine` proxy address:

- `creditDeposit()` calls `endpoint.depositCollateralWithReferral()` on the deprecated old proxy, which may be paused, decommissioned, or simply no longer the active settlement contract.
- The call reverts. WETH (converted from user ETH via `receive()`) accumulates in `DirectDepositV1`.
- Users have no self-service recovery path. Only the owner can call `withdraw(IIERC20Base token)` to extract the stuck WETH.
- If the owner does not promptly act, or if the migration is not communicated to users, funds are effectively locked in the contract.
- The broken invariant: `creditDeposit()` is designed to be callable by anyone to route held balances into the protocol. After migration, this invariant is permanently violated with no on-chain mechanism to restore it.

---

### Likelihood Explanation

The `ProxyManager` system explicitly supports registering new proxies via `registerRegularProxy()`. A major protocol upgrade (e.g., a new `Endpoint` version requiring a new proxy) is a realistic and expected lifecycle event. `DirectDepositV1` is a peripheral contract that is entirely outside the `ProxyManager` upgrade registry, making it structurally unable to track such changes. Any user who sends ETH to `DirectDepositV1` after such a migration — without knowing the migration occurred — will have their funds stuck.

---

### Recommendation

Add owner-restricted setter functions for `endpoint`, `spotEngine`, and `wrappedNative` in `DirectDepositV1`, with appropriate validation (e.g., confirming the new endpoint accepts deposits before switching). Alternatively, make `DirectDepositV1` upgradeable using the OpenZeppelin upgradeable proxy pattern consistent with the rest of the Nado protocol. At minimum, emit an event when the contract is deployed so integrators can detect when a new instance must be used.

---

### Proof of Concept

1. Protocol deploys a new `Endpoint` proxy at address `newEndpoint` via `ProxyManager.registerRegularProxy("Endpoint", newEndpoint)`.
2. User sends 1 ETH to `DirectDepositV1`. `receive()` immediately calls `wrappedNative.call{value: 1 ether}("")`, converting it to 1 WETH held in the contract.
3. User (or anyone) calls `creditDeposit()`.
4. Line 84: `spotEngine.getProductIds()` — succeeds if old SpotEngine proxy is still live.
5. Line 92: `token.approve(address(endpoint), balance)` — approves the old, deprecated `Endpoint`.
6. Lines 93–98: `endpoint.depositCollateralWithReferral(subaccount, productId, amount, "-1")` — calls the deprecated old `Endpoint`, which is no longer the active settlement contract and reverts.
7. The entire `creditDeposit()` call reverts. 1 WETH remains in `DirectDepositV1`.
8. The user has no recovery function. Only the owner can call `withdraw(wethToken)` to extract the funds. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L42-51)
```text
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

**File:** core/contracts/DirectDepositV1.sol (L64-67)
```text
    receive() external payable {
        (bool success, ) = wrappedNative.call{value: msg.value}("");
        require(success, "Failed to wrap native token.");
    }
```

**File:** core/contracts/DirectDepositV1.sol (L83-100)
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
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
