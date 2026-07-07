### Title
Unchecked `transferFrom()` Return Value in `replaceUsdcEWithUsdc()` Enables usdcE Theft from DirectDepositV1 — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc()` calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` as a raw interface call whose boolean return value is never checked. The function has no access control, so any unprivileged caller can invoke it. If the USDC token at the hardcoded address returns `false` on a failed transfer instead of reverting (non-compliant ERC20 behavior), execution silently continues, and the function proceeds to withdraw all usdcE from the victim's `DirectDepositV1` and send it to the caller — with no USDC actually deposited.

---

### Finding Description

`ContractOwner` imports and uses `ERC20Helper for IERC20Base` throughout the contract, which provides both `safeTransfer()` and `safeTransferFrom()` wrappers that perform a low-level `call()` and assert the return value. [1](#0-0) 

However, in `replaceUsdcEWithUsdc()`, the inbound USDC leg uses a raw `transferFrom()` call:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
``` [2](#0-1) 

The return value of this call is discarded. Immediately after, the outbound usdcE leg correctly uses `safeTransfer()`:

```solidity
IERC20Base(usdcE).safeTransfer(msg.sender, balance);
``` [3](#0-2) 

This asymmetry means the contract enforces the outbound transfer but not the inbound one. The function carries no `onlyOwner` or `onlyDeployer` modifier — it is callable by any external account. [4](#0-3) 

The intermediate step — `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` — transfers the full usdcE balance from the `DirectDepositV1` contract to `ContractOwner`, after which `safeTransfer` sends it to `msg.sender`. [5](#0-4) 

`DirectDepositV1.withdraw()` is `onlyOwner`, and `ContractOwner` is its owner, so this step always succeeds once called. [6](#0-5) 

---

### Impact Explanation

If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` (Ink mainnet, chain 57073) returns `false` on a failed `transferFrom` rather than reverting, an attacker can drain the full usdcE balance from any `DirectDepositV1` address that holds usdcE, receiving real tokens without providing any USDC. The corrupted asset delta is: attacker gains `balance` usdcE; the `DirectDepositV1` loses `balance` usdcE; no USDC is deposited.

---

### Likelihood Explanation

The function is permissionless (no access modifier). The only gate is `block.chainid == 57073` and a non-zero `directDepositV1Address[subaccount]`. Any user can call it. The exploitability depends on whether the specific USDC token at the hardcoded address is non-compliant (returns `false` instead of reverting). Many bridged or wrapped stablecoin deployments on newer chains do not follow the strict revert-on-failure pattern. The inconsistency — `safeTransfer` used for usdcE but raw `transferFrom` used for USDC in the same function — indicates this was an oversight rather than a deliberate design choice.

---

### Recommendation

Replace the raw `transferFrom` call with `safeTransferFrom` from `ERC20Helper`, which is already imported and in scope via `using ERC20Helper for IERC20Base`:

```solidity
// Before (line 616):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After:
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [7](#0-6) 

---

### Proof of Concept

1. A `DirectDepositV1` exists for some `subaccount` and holds `N` usdcE tokens.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC allowance.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, N)` returns `false` (non-compliant token); return value is ignored; execution continues.
4. `DirectDepositV1.withdraw(usdcE)` transfers `N` usdcE to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, N)` sends `N` usdcE to the attacker.
6. Attacker receives `N` usdcE having provided zero USDC. [8](#0-7)

### Citations

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

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
