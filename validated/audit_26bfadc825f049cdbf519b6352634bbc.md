### Title
Native ETH Accounting Mismatch Between `isDirectDepositV1Ready` and `creditDeposit` Causes ETH Lock in DDA — (File: `core/contracts/ContractOwner.sol`, `core/contracts/DirectDepositV1.sol`)

---

### Summary

`ContractOwner.isDirectDepositV1Ready()` includes the DDA's raw native ETH balance in its deposit-readiness check, but `DirectDepositV1.creditDeposit()` only iterates over ERC20 product-token balances and never wraps or deposits native ETH. When native ETH lands in a DDA without being wrapped — via a silent constructor wrapping failure or a `selfdestruct` injection — the readiness oracle signals "ready," the off-chain system calls `creditDeposit()`, and the ETH is silently skipped, leaving it permanently locked in the DDA with no subaccount credit issued.

---

### Finding Description

**Root cause — readiness check counts native ETH:**

In `ContractOwner.isDirectDepositV1Ready()`, when the iterated product token is `wrappedNative`, the function adds the DDA's raw native ETH balance on top of its ERC20 balance before evaluating the minimum deposit threshold: [1](#0-0) 

```solidity
uint256 balance = token.balanceOf(recipient);
if (tokenAddr == wrappedNative) {
    balance += recipient.balance;   // native ETH counted here
}
```

**Root cause — deposit function ignores native ETH:**

`DirectDepositV1.creditDeposit()` iterates over all product IDs and deposits only ERC20 balances. There is no step to wrap or deposit native ETH: [2](#0-1) 

```solidity
uint256 balance = token.balanceOf(address(this));   // ERC20 only
if (balance != 0) {
    token.approve(address(endpoint), balance);
    endpoint.depositCollateralWithReferral(...);
}
```

**Two concrete paths that leave native ETH unwrapped in the DDA:**

**Path 1 — Constructor wrapping failure (silent):**
The `DirectDepositV1` constructor attempts to wrap any pre-existing ETH balance at the CREATE2 address, but explicitly does not revert on failure: [3](#0-2) 

```solidity
uint256 balance = address(this).balance;
if (balance != 0) {
    (bool success, ) = wrappedNative.call{value: balance}("");
    if (!success) {
        emit NativeTokenTransferFailed(balance);   // silent — ETH stays
    }
}
```

If the WETH contract is temporarily unavailable, or if the DDA's deterministic CREATE2 address was pre-funded before deployment, the ETH remains unwrapped in the DDA after construction.

**Path 2 — `selfdestruct` injection (bypasses `receive()`):**
The `receive()` function wraps ETH to WETH on normal transfers: [4](#0-3) 

```solidity
receive() external payable {
    (bool success, ) = wrappedNative.call{value: msg.value}("");
    require(success, "Failed to wrap native token.");
}
```

However, a `selfdestruct` call targeting the DDA address bypasses `receive()` entirely, forcing raw ETH into the contract without triggering the wrapping logic.

**The broken invariant:**

After either path, the DDA holds native ETH. `isDirectDepositV1Ready()` returns `true` (because `recipient.balance > 0` pushes the balance above the minimum threshold). The off-chain system calls `ContractOwner.creditDepositV1()` → `DirectDepositV1.creditDeposit()`. `creditDeposit()` finds zero WETH balance (the ETH was never wrapped), deposits nothing, and returns silently. The user's subaccount receives no credit, and the ETH is locked in the DDA.

The `createDirectDepositV1` call uses a fixed salt, making the DDA address fully deterministic and predictable: [5](#0-4) 

---

### Impact Explanation

Native ETH deposited to a DDA (either by the user pre-funding the CREATE2 address, or injected via `selfdestruct`) is permanently locked in the DDA with no subaccount credit issued. The user's collateral balance in the protocol does not reflect the ETH they sent. Recovery requires the owner to manually call `withdrawFromDirectDepositV1(subaccount, address(0))`, which pulls the ETH out to the owner rather than crediting the user's subaccount — meaning the user still does not receive their deposit credit automatically. [6](#0-5) 

---

### Likelihood Explanation

The constructor wrapping failure path is realistic whenever the WETH contract is temporarily non-functional at DDA deployment time, or when a user pre-funds the deterministic CREATE2 address before `createDirectDepositV1` is called. The `selfdestruct` path is available to any on-chain actor willing to burn ETH. Both paths are unprivileged and externally reachable. The DDA address is fully predictable from the subaccount bytes32 and the fixed salt.

---

### Recommendation

Add native ETH wrapping to `creditDeposit()` before the ERC20 deposit loop, mirroring the logic already present in `receive()`:

```solidity
function creditDeposit() external {
    // Wrap any native ETH that bypassed receive() (e.g., selfdestruct or constructor failure)
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance != 0) {
        (bool success, ) = wrappedNative.call{value: nativeBalance}("");
        require(success, "Failed to wrap native token.");
    }

    uint32[] memory productIds = spotEngine.getProductIds();
    // ... existing ERC20 loop unchanged
}
```

Alternatively, remove `recipient.balance` from `isDirectDepositV1Ready()` so the readiness check never signals true for unwrapped ETH that `creditDeposit()` cannot handle.

---

### Proof of Concept

1. Compute the deterministic DDA address for a target `subaccount` using the known CREATE2 parameters (factory = `ContractOwner`, salt = `bytes32(uint256(1))`, initcode = `DirectDepositV1` creation code).
2. Deploy an attacker contract holding 1 ETH and call `selfdestruct(ddaAddress)`. The DDA's `receive()` is bypassed; `address(dda).balance == 1 ether`, `WETH.balanceOf(dda) == 0`.
3. Call `ContractOwner.isDirectDepositV1Ready(ddaAddress, false)`. The function evaluates `balance = WETH.balanceOf(dda) + dda.balance = 0 + 1e18`, passes the minimum deposit check, and returns `true`.
4. Call `ContractOwner.creditDepositV1(subaccount)`. This calls `DirectDepositV1.creditDeposit()`.
5. Inside `creditDeposit()`, for the `wrappedNative` product: `token.balanceOf(address(this)) == 0` → condition is false → no deposit is made.
6. The 1 ETH remains locked in the DDA. The subaccount receives zero credit. The off-chain system observes a false-positive readiness signal with no resulting deposit. [7](#0-6) [2](#0-1)

### Citations

**File:** core/contracts/ContractOwner.sol (L495-498)
```text
        DirectDepositV1 directDepositV1 = new DirectDepositV1{
            salt: bytes32(uint256(1))
        }(address(endpoint), address(spotEngine), subaccount, wrappedNative);
        directDepositV1Address[subaccount] = payable(directDepositV1);
```

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

**File:** core/contracts/ContractOwner.sol (L574-578)
```text
            IERC20Base token = IERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(recipient);
            if (tokenAddr == wrappedNative) {
                balance += recipient.balance;
            }
```

**File:** core/contracts/ContractOwner.sol (L628-636)
```text
        if (token == address(0)) {
            uint256 preBalance = address(this).balance;
            DirectDepositV1(directDepositV1).withdrawNative();
            uint256 postBalance = address(this).balance;
            require(postBalance > preBalance, "empty");
            (bool success, ) = msg.sender.call{value: postBalance - preBalance}(
                ""
            );
            require(success, "xfer");
```

**File:** core/contracts/DirectDepositV1.sol (L52-60)
```text
        uint256 balance = address(this).balance;
        if (balance != 0) {
            // shouldn't revert even if the transfer fails, otherwise the funds
            // will be stuck in the DDA forever.
            (bool success, ) = wrappedNative.call{value: balance}("");
            if (!success) {
                emit NativeTokenTransferFailed(balance);
            }
        }
```

**File:** core/contracts/DirectDepositV1.sol (L64-67)
```text
    receive() external payable {
        (bool success, ) = wrappedNative.call{value: msg.value}("");
        require(success, "Failed to wrap native token.");
    }
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
