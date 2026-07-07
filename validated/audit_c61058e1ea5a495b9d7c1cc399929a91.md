### Title
Unrestricted `receive()` in `DirectDepositV1` Permanently Redirects Any Caller's ETH to a Fixed Subaccount — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.receive()` accepts native ETH from **any** unprivileged caller with no `msg.sender` guard. The ETH is immediately wrapped into WETH and held in the contract. Because `creditDeposit()` is also callable by anyone, the wrapped ETH is deposited into the DDA's hardcoded `subaccount` — not the sender's. The sender has no recovery path: `withdraw()` and `withdrawNative()` are both `onlyOwner`.

---

### Finding Description

`receive()` at line 64 forwards all incoming ETH to `wrappedNative` unconditionally:

```solidity
receive() external payable {
    (bool success, ) = wrappedNative.call{value: msg.value}("");
    require(success, "Failed to wrap native token.");
}
``` [1](#0-0) 

There is no check on `msg.sender`. The WETH minted by this call is credited back to the DDA contract itself (the DDA is the `msg.sender` to `wrappedNative`), so the WETH accumulates inside the DDA.

`creditDeposit()` is then callable by **anyone** with no access control:

```solidity
function creditDeposit() external {
    ...
    endpoint.depositCollateralWithReferral(
        subaccount,   // hardcoded at construction
        productId,
        uint128(balance),
        "-1"
    );
``` [2](#0-1) 

It sweeps the entire WETH balance into the DDA's fixed `subaccount`. The only recovery functions are gated:

```solidity
function withdraw(IIERC20Base token) external onlyOwner { ... }
function withdrawNative() external onlyOwner { ... }
``` [3](#0-2) 

---

### Impact Explanation

Any user who sends ETH to the DDA — whether by mistake or by being directed to the address — permanently loses those funds. The ETH is wrapped and deposited into the DDA owner's `subaccount`, not the sender's. The sender has zero on-chain recourse: they cannot call `withdraw()` or `withdrawNative()`, and even if `creditDeposit()` is never called, the WETH sits in the contract until the owner extracts it for themselves. This is a direct, irreversible asset loss for the unprivileged caller.

**Impact: High** — ETH sent by any caller is permanently redirected to a subaccount the caller does not control.

---

### Likelihood Explanation

DDAs are deployed per-user and their addresses are shared as deposit targets. A user who sends native ETH directly (e.g., from a wallet UI, a CEX withdrawal, or a script that resolves the DDA address) will trigger `receive()` without any warning. The `creditDeposit()` call can be front-run or called by a keeper at any time, making the misattribution irreversible before the user can react.

**Likelihood: Medium** — Native ETH transfers to contract addresses are a common user action; the DDA address is a known deposit target.

---

### Recommendation

Restrict `receive()` to only accept ETH from the `wrappedNative` contract (i.e., during WETH unwrap callbacks) or from the designated subaccount owner. Add a `msg.sender` guard:

```solidity
receive() external payable {
    require(msg.sender == wrappedNative, "Only wrappedNative");
    // or: require(msg.sender == owner(), "Only owner");
}
```

If the DDA is intentionally designed to accept native ETH from users, add a `creditDeposit()` access control so only the subaccount owner (or a trusted keeper) can trigger the sweep, preventing immediate irreversible misattribution.

---

### Proof of Concept

```solidity
// Attacker or mistaken user sends ETH directly to the DDA
address(directDepositV1).transfer(10 ether);
// ETH is wrapped → WETH held by DDA

// Anyone (attacker, keeper, or the user themselves) calls creditDeposit()
directDepositV1.creditDeposit();
// WETH is now deposited into the DDA's hardcoded subaccount
// Sender's 10 ETH is gone — credited to a subaccount they do not own
// withdraw() and withdrawNative() are onlyOwner — sender has no recourse
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** core/contracts/DirectDepositV1.sol (L103-112)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }

    function withdrawNative() external onlyOwner {
        uint256 balance = address(this).balance;
        (bool success, ) = msg.sender.call{value: balance}("");
        require(success, "Failed to transfer native token to owner");
    }
```
