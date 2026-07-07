### Title
Unsafe Raw `transferFrom` Without Return Value Check Enables Potential usdcE Drain — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` performs a raw `.transferFrom()` call on the USDC token without checking its return value and without using the project's own `ERC20Helper.safeTransferFrom` wrapper. Because the function is externally callable by any user on chain 57073 with no privilege gate, a silent failure of the `transferFrom` (return `false` instead of revert) allows the caller to receive usdcE tokens from a `DirectDepositV1` contract without providing any USDC in exchange.

---

### Finding Description

`ContractOwner` declares `using ERC20Helper for IERC20Base` and uses `safeTransfer`/`safeTransferFrom` throughout the codebase. However, `replaceUsdcEWithUsdc` breaks this pattern at line 616:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

This is a direct call to the raw ERC20 `transferFrom` interface method. The return value (`bool`) is silently discarded. If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink chain returns `false` on failure rather than reverting (a valid ERC20 behavior), execution continues to the next two lines:

```solidity
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));  // pulls usdcE to ContractOwner
IERC20Base(usdcE).safeTransfer(msg.sender, balance);             // sends usdcE to attacker
```

`DirectDepositV1.withdraw` is `onlyOwner`, and `ContractOwner` is the owner of every `DirectDepositV1` it deploys. So the withdraw succeeds unconditionally, transferring usdcE to `ContractOwner`, which then forwards it to the attacker via `safeTransfer`.

The function has no privilege modifier — only a chain ID check:

```solidity
function replaceUsdcEWithUsdc(bytes32 subaccount) external {
    require(block.chainid == 57073, ERR_UNAUTHORIZED);
```

Any EOA or contract on Ink chain can call it. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

If the USDC token on Ink chain (chain 57073) returns `false` on a failed `transferFrom` (e.g., insufficient allowance or balance) rather than reverting:

- The attacker provides zero USDC.
- The `DirectDepositV1` contract's entire usdcE balance is drained to the attacker.
- The corrupted asset delta: usdcE balance of `directDepositV1[subaccount]` → 0, with no corresponding USDC deposited.
- Any subaccount that had usdcE staged for deposit loses those funds permanently. [5](#0-4) 

---

### Likelihood Explanation

The Ink chain USDC at the hardcoded address is a non-canonical bridged token whose exact implementation behavior is not guaranteed to revert on failure. The ERC20 standard only requires a `bool` return — returning `false` is fully spec-compliant. The function is permissionlessly callable by any address on chain 57073. The rest of the codebase consistently uses `safeTransferFrom` for exactly this reason, making this a clear deviation from the established safe pattern. [6](#0-5) [7](#0-6) 

---

### Recommendation

Replace the raw `transferFrom` call with the project's existing `safeTransferFrom` wrapper from `ERC20Helper`:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`ERC20Helper.safeTransferFrom` is already imported and the `using ERC20Helper for IERC20Base` directive is active in `ContractOwner`, so this is a one-word fix consistent with every other transfer in the protocol. [8](#0-7) [2](#0-1) 

---

### Proof of Concept

1. A `DirectDepositV1` contract for `subaccount` holds `N` usdcE tokens (staged for deposit).
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` on Ink chain with zero USDC allowance granted to `ContractOwner`.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, N)` returns `false` (insufficient allowance) — return value not checked, execution continues.
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` — `ContractOwner` is owner, succeeds; `N` usdcE transferred to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, N)` — `N` usdcE sent to attacker.
6. Result: attacker receives `N` usdcE, `directDepositV1` balance is 0, no USDC was ever transferred. [1](#0-0) [4](#0-3)

### Citations

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
    using ERC20Helper for IERC20Base;
```

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```

**File:** core/contracts/EndpointStorage.sol (L95-101)
```text
    function safeTransferFrom(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal virtual {
        token.safeTransferFrom(from, address(this), amount);
    }
```
