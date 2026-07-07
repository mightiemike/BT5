### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables usdcE Drain Without USDC Payment — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` without checking the return value. If the USDC token's `transferFrom` returns `false` instead of reverting, execution continues and the caller receives usdcE from the target DDA without having provided any USDC. The Nado-native safe-transfer wrapper `ERC20Helper.safeTransferFrom` is imported and active in the same contract but is not used at this call site.

---

### Finding Description

`replaceUsdcEWithUsdc` is an `external` function with no access control beyond a chain-ID gate (`block.chainid == 57073`). Its intended logic is a 1:1 swap: pull USDC from the caller into the DDA, then withdraw usdcE from the DDA back to the caller.

```
// core/contracts/ContractOwner.sol  lines 608-620
function replaceUsdcEWithUsdc(bytes32 subaccount) external {
    require(block.chainid == 57073, ERR_UNAUTHORIZED);
    address payable directDepositV1 = directDepositV1Address[subaccount];
    require(directDepositV1 != address(0), "no dda");
    address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
    address usdc  = 0x2D270e6886d130D724215A266106e6832161EAEd;
    uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
    if (balance > 0) {
        IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance); // ← return value silently discarded
        DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
        IERC20Base(usdcE).safeTransfer(msg.sender, balance);                 // ← usdcE sent regardless
    }
}
``` [1](#0-0) 

The `IERC20Base` interface declares `transferFrom` as returning `bool`. [2](#0-1) 

The return value at line 616 is never inspected. If the token returns `false` (rather than reverting), the three-step sequence continues: the DDA's usdcE is withdrawn to `ContractOwner` and then forwarded to the caller via `safeTransfer`, while no USDC was actually received.

The rest of the codebase consistently uses `ERC20Helper.safeTransferFrom`, which low-level-calls the token and requires `success && (data.length == 0 || abi.decode(data, (bool)))`. [3](#0-2) 

`ContractOwner` even declares `using ERC20Helper for IERC20Base` at the top of the file, making `safeTransferFrom` directly available on the `IERC20Base` type, yet the raw interface call is used instead. [4](#0-3) 

`BaseWithdrawPool` demonstrates the correct pattern: every outbound and inbound token movement goes through `ERC20Helper`. [5](#0-4) 

---

### Impact Explanation

An attacker who calls `replaceUsdcEWithUsdc` for any subaccount whose DDA holds usdcE, while the USDC `transferFrom` silently returns `false`, receives the full usdcE balance of that DDA at zero cost. The corrupted asset delta is: usdcE balance of the victim DDA → attacker wallet, with no corresponding USDC credit to the DDA. The subaccount owner loses their bridged usdcE collateral.

---

### Likelihood Explanation

The function is callable by any unprivileged address on Ink chain (chainid 57073) with no further gate. The hardcoded USDC address (`0x2D270e6886d130D724215A266106e6832161EAEd`) is a deployment-specific contract whose exact revert-vs-return-false behavior on Ink is not guaranteed to match mainnet Circle USDC. Any USDC implementation that returns `false` on an insufficient-allowance `transferFrom` (rather than reverting) directly enables the drain. Even if the current deployment reverts, the unchecked pattern is a latent vulnerability that activates if the token address is ever pointed at a non-reverting ERC20.

---

### Recommendation

Replace the raw `transferFrom` call with the already-imported `ERC20Helper.safeTransferFrom`:

```solidity
// Before (vulnerable)
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe)
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

This mirrors the pattern used in `BaseWithdrawPool.safeTransferFrom` and `ERC20Helper.safeTransferFrom`, which both handle the zero-return-data case correctly.

---

### Proof of Concept

1. A subaccount DDA on Ink chain holds `N` usdcE tokens.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC allowance granted to `ContractOwner`.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, N)` is executed. If the USDC token returns `false` instead of reverting, the call succeeds at the Solidity level with no revert.
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` transfers all `N` usdcE from the DDA to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, N)` sends all `N` usdcE to the attacker.
6. Attacker holds `N` usdcE; DDA holds zero; no USDC was ever transferred. [6](#0-5)

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

**File:** core/contracts/interfaces/IERC20Base.sol (L25-29)
```text
    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool);
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

**File:** core/contracts/BaseWithdrawPool.sol (L192-198)
```text
    function safeTransferFrom(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal virtual {
        token.safeTransferFrom(from, address(this), amount);
    }
```
